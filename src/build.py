#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py -- 中国汽车销量看板构建脚本

读取 data/sales.csv，生成一个完全自包含的单文件 HTML 看板
（ECharts 库与全部数据均内联，打开后零外部请求）到 docs/index.html。

用法: python3 build.py
"""

import csv
import json
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request
import ssl
from datetime import datetime, timezone

import html as _html_mod
def _esc(s):
    """HTML 转义，用于把 Python 端字符串安全地拼进静态模板文本。"""
    return _html_mod.escape(str(s), quote=True)

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
# 关键：不能用 SCRIPT_DIR（脚本文件自身所在目录）来定位 docs/ / vendor/ / data/ ——
# 脚本现在存放在仓库的 src/ 目录下，如果继续用 SCRIPT_DIR，docs/ 会被写到
# <repo>/src/docs/ 而不是 <repo>/docs/，GitHub Pages（配置为发布 /docs）会直接失效。
# 统一改用 REPO_ROOT = 运行时的当前工作目录：workflow 里固定是先 cd 到仓库根，
# 再执行 python3 src/build.py，所以 os.getcwd() 就是仓库根，且在脚本搬到 src/ 之后、
# 或者以后再搬到别的地方，都不需要再改这里。
REPO_ROOT = os.getcwd()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # 仅保留供参考/调试，不再用于定位输出路径
def _first_existing(*candidates):
    """按顺序返回第一个存在的路径；都不存在时返回第一个候选（供报错信息使用）。"""
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return candidates[0]

# 数据源：环境变量优先，其次相对当前工作目录（GitHub Actions / 本地都应该在仓库根下运行），
# 再次显式相对仓库根（REPO_ROOT，效果上和上一条一样，是防御性的双重保险）。
# 绝不硬编码开发环境的绝对路径，也不再依赖脚本文件自身的位置。
CSV_PATH = os.environ.get("SALES_CSV") or _first_existing(
    os.path.join("data", "sales.csv"),
    os.path.join(REPO_ROOT, "data", "sales.csv"),
)
# 新闻缓存：可选，不存在时优雅降级
NEWS_PATH = os.environ.get("NEWS_JSON") or _first_existing(
    os.path.join("data", "news.json"),
    os.path.join(REPO_ROOT, "data", "news.json"),
    os.path.join(REPO_ROOT, "news.json"),
)

# 实时动态检索接口地址（可选）。留空 "" 时：看板不显示"查最新"按钮，
# 只展示 news.json 预生成的快照，不发任何实时请求、不报错、不显示坏掉的按钮。
# 部署好 /api/dynamics 后端后，把这里换成它的域名（不含末尾斜杠），重新构建即可生效。
# ===== API_BASE_BEGIN =====
DYNAMICS_API_BASE = "https://car-industry-monitor.vercel.app"
# ===== API_BASE_END =====

VENDOR_ECHARTS = os.path.join(REPO_ROOT, "vendor", "echarts.min.js")
OUT_DIR = os.path.join(REPO_ROOT, "docs")
OUT_PATH = os.path.join(OUT_DIR, "index.html")

BODY_TYPES = ["SUV", "轿车", "MPV", "其他", "运动汽车"]  # 按数据量从大到小排列

# ===== CALIBER_NOTE_BEGIN =====
CALIBER_SHORT = "口径：经比对推断为乘联会零售数据"
CALIBER_LONG = (
    "本看板销量数据采集自车主之家（16888.com）月度销量排行榜，该站未在页面上标注统计口径。经与乘联会（CPCA）2024–2026 年 6 个月份的官方零售、批发数据逐一比对，本数据与乘联会「全国乘用车市场零售」口径的月度总量差异稳定在 ±0.8% 以内，而与「厂商批发」口径差异达 -2.6%～-31.7% 且随时间扩大，因此推断为零售口径（车企/经销商卖给终端消费者的数量），而非发给经销商的批发出货量。提醒：零售口径通常低于同期批发口径（年末冲量月份尤为明显），也与交强险上牌量、中汽协产销数据存在统计范围差异；跨数据源比较时请先对齐口径，不要直接拿本看板数字与批发/产销类新闻标题比较。本判定由外部权威数据反推得出，非数据源官方声明，置信度高但非 100% 确定。另注：数据源声明其销量不包含进口车型。"
)
# ===== CALIBER_NOTE_END =====


# ---------------------------------------------------------------------------
# 1. 读取 & 聚合数据
# ---------------------------------------------------------------------------
def ym_index(year, month):
    """把 (year, month) 映射为从 2024-01 开始的连续月份序号 (0-based)。"""
    return (int(year) - 2024) * 12 + (int(month) - 1)


def load_rows():
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def build_dim_table(rows, keyfunc, n_months):
    """
    通用聚合：按 keyfunc(row) 分组，产出：
      names: [实体名 ...]（按首次出现顺序）
      fuel:  [[月度燃油销量 x n_months], ...]  与 names 对应
      ev:    [[月度新能源销量 x n_months], ...]
    """
    order = []
    fuel_map = {}
    ev_map = {}
    for r in rows:
        k = keyfunc(r)
        if k not in fuel_map:
            order.append(k)
            fuel_map[k] = [0] * n_months
            ev_map[k] = [0] * n_months
        idx = ym_index(r["year"], r["month"])
        sales = int(r["sales"])
        if r["energy_type"] == "燃油":
            fuel_map[k][idx] += sales
        else:
            ev_map[k][idx] += sales
    return order, fuel_map, ev_map


def aggregate(rows):
    years = sorted(set(int(r["year"]) for r in rows))
    months_all = [(int(r["year"]), int(r["month"])) for r in rows]
    min_ym = min(months_all)
    max_ym = max(months_all)
    n_months = ym_index(max_ym[0], max_ym[1]) + 1

    # 厂商粒度
    manu_order, manu_fuel, manu_ev = build_dim_table(
        rows, lambda r: r["manufacturer"], n_months
    )
    # 品牌粒度
    brand_order, brand_fuel, brand_ev = build_dim_table(
        rows, lambda r: r["brand"], n_months
    )
    # 车体类型 -> 车型粒度（key 为 (body_type, model) 元组，保证跨车体同名车型不冲突）
    model_order, model_fuel, model_ev = build_dim_table(
        rows, lambda r: (r["body_type"], r["model"]), n_months
    )

    def pack(order, fuel_map, ev_map, name_fn):
        return {
            "n": [name_fn(k) for k in order],
            "f": [fuel_map[k] for k in order],
            "e": [ev_map[k] for k in order],
        }

    manu_payload = pack(manu_order, manu_fuel, manu_ev, lambda k: k)
    brand_payload = pack(brand_order, brand_fuel, brand_ev, lambda k: k)
    model_payload = pack(model_order, model_fuel, model_ev, lambda k: k[1])
    model_body_idx = [BODY_TYPES.index(k[0]) for k in model_order]

    # 每个 (body_type, model) 在源数据里唯一对应一个厂商与一个品牌（已用全量数据校验，
    # 见开发记录）；这里把这层归属关系也编码进车型数组，供前端做"统计范围"审计视图，
    # 不需要在浏览器端反查 mapping.json。用 dict 建立 (body_type, model) -> 厂商/品牌名，
    # 顺带校验假设是否仍然成立——如果未来数据出现同名车型分属多个厂商，构建时立刻报错，
    # 而不是让前端悄悄展示错误的归属。
    model_manu_name = {}
    model_brand_name = {}
    for r in rows:
        k = (r["body_type"], r["model"])
        mfr, brd = r["manufacturer"], r["brand"]
        if k in model_manu_name and model_manu_name[k] != mfr:
            raise SystemExit(
                f"数据假设被打破: 车型 {k} 同时属于厂商 {model_manu_name[k]!r} 和 {mfr!r}，"
                f"需要调整「统计范围」的实现（不能再假设车型与厂商一一对应）。"
            )
        if k in model_brand_name and model_brand_name[k] != brd:
            raise SystemExit(
                f"数据假设被打破: 车型 {k} 同时属于品牌 {model_brand_name[k]!r} 和 {brd!r}。"
            )
        model_manu_name[k] = mfr
        model_brand_name[k] = brd
    manu_index = {name: i for i, name in enumerate(manu_order)}
    brand_index = {name: i for i, name in enumerate(brand_order)}
    model_manu_idx = [manu_index[model_manu_name[k]] for k in model_order]
    model_brand_idx = [brand_index[model_brand_name[k]] for k in model_order]

    # 一个厂商内含多个品牌的情况（按车型逐条拆分得到，用于"关于数据"里的说明文字，
    # 不是硬编码列表——数据变了这句话会跟着变）。
    manu_to_brands = {}
    for r in rows:
        manu_to_brands.setdefault(r["manufacturer"], set()).add(r["brand"])
    multi_brand_manus = sorted(
        [m for m, bs in manu_to_brands.items() if len(bs) > 1],
        key=lambda m: -len(manu_to_brands[m]),
    )

    payload = {
        "manu": manu_payload,
        "brand": brand_payload,
        "model": model_payload,
        "modelBody": model_body_idx,
        "modelManu": model_manu_idx,
        "modelBrand": model_brand_idx,
        "bodyTypes": BODY_TYPES,
        "nMonths": n_months,
        "startYear": min_ym[0],
        "startMonth": min_ym[1],
    }

    multi_brand_desc = "、".join(
        f"{m}（{len(manu_to_brands[m])}个品牌：{'/'.join(sorted(manu_to_brands[m]))}）"
        for m in multi_brand_manus
    ) if multi_brand_manus else "（当前数据中未发现一厂多牌的情况）"

    meta = {
        "rows": len(rows),
        "manufacturers": len(manu_order),
        "brands": len(brand_order),
        "models": len(set(k[1] for k in model_order)),
        "years": years,
        "coverageStartYear": min_ym[0],
        "coverageStartMonth": min_ym[1],
        "coverageEndYear": max_ym[0],
        "coverageEndMonth": max_ym[1],
        "buildTime": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
        "multiBrandManufacturers": multi_brand_manus,
        "multiBrandDesc": multi_brand_desc,
    }
    return payload, meta


# ---------------------------------------------------------------------------
# 2. 获取 ECharts（内联优先，失败则回退 CDN <script> 标签）
# ---------------------------------------------------------------------------
def _try_npm_pack():
    """通过 npm registry 拉取 echarts 包（该环境的出网策略允许 registry.npmjs.org）。"""
    npm = shutil.which("npm")
    if not npm:
        return None
    tmp = tempfile.mkdtemp(prefix="echarts_npm_")
    try:
        subprocess.run(
            [npm, "pack", "echarts@5", "--silent"],
            cwd=tmp, check=True, capture_output=True, timeout=120,
        )
        tgz = None
        for fn in os.listdir(tmp):
            if fn.endswith(".tgz"):
                tgz = os.path.join(tmp, fn)
                break
        if not tgz:
            return None
        subprocess.run(
            ["tar", "xzf", tgz, "package/dist/echarts.min.js"],
            cwd=tmp, check=True, capture_output=True, timeout=60,
        )
        js_path = os.path.join(tmp, "package", "dist", "echarts.min.js")
        if os.path.isfile(js_path):
            with open(js_path, "r", encoding="utf-8") as f:
                return f.read()
    except Exception as e:
        print(f"  npm pack 获取 echarts 失败: {e}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


def _try_cdn_download():
    urls = [
        "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js",
        "https://unpkg.com/echarts@5/dist/echarts.min.js",
        "https://registry.npmmirror.com/echarts/5.6.0/files/dist/echarts.min.js",
    ]
    ctx = ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt") if os.path.exists(
        "/root/.ccr/ca-bundle.crt"
    ) else None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                data = resp.read()
                if len(data) > 200000:  # 粗略校验拿到的是完整文件而非错误页
                    return data.decode("utf-8")
        except Exception as e:
            print(f"  CDN 下载失败 {url}: {e}", file=sys.stderr)
    return None


def get_echarts():
    """
    返回 (mode, content):
      mode == 'inline' -> content 是完整 JS 源码，写入 <script>...</script>
      mode == 'cdn'     -> content 是 CDN URL，写入 <script src="...">
    优先级: 本地 vendor 缓存 -> npm pack -> 直接 CDN 下载 -> 回退 CDN <script> 标签
    """
    if os.path.isfile(VENDOR_ECHARTS):
        print("  使用本地缓存 vendor/echarts.min.js")
        with open(VENDOR_ECHARTS, "r", encoding="utf-8") as f:
            return "inline", f.read()

    print("  本地无缓存，尝试通过 npm registry 下载 echarts ...")
    js = _try_npm_pack()
    if js:
        os.makedirs(os.path.dirname(VENDOR_ECHARTS), exist_ok=True)
        with open(VENDOR_ECHARTS, "w", encoding="utf-8") as f:
            f.write(js)
        return "inline", js

    print("  npm 不可用，尝试直接从 CDN 下载 ...")
    js = _try_cdn_download()
    if js:
        os.makedirs(os.path.dirname(VENDOR_ECHARTS), exist_ok=True)
        with open(VENDOR_ECHARTS, "w", encoding="utf-8") as f:
            f.write(js)
        return "inline", js

    print("  !! 所有内联方式均失败，回退为 CDN <script> 标签（生成的 HTML 将非完全离线）")
    return "cdn", "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"


# ---------------------------------------------------------------------------
# 3. 新闻数据（可选，不存在时给空对象，绝不报错）
# ---------------------------------------------------------------------------
def load_news():
    if os.path.isfile(NEWS_PATH):
        try:
            with open(NEWS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  警告: news.json 存在但解析失败，忽略: {e}", file=sys.stderr)
            return {}
    return {}


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
def main():
    print(f"读取 CSV: {CSV_PATH}")
    rows = load_rows()
    print(f"  {len(rows)} 行")

    print("聚合数据 (厂商 / 品牌 / 车体类型->车型) ...")
    payload, meta = aggregate(rows)
    print(f"  厂商 {meta['manufacturers']} 个，品牌 {meta['brands']} 个，车型 {meta['models']} 个")
    print(f"  覆盖 {meta['coverageStartYear']}-{meta['coverageStartMonth']:02d} "
          f"至 {meta['coverageEndYear']}-{meta['coverageEndMonth']:02d}")

    print("获取 ECharts ...")
    echarts_mode, echarts_content = get_echarts()

    print("加载 news.json（可选）...")
    news = load_news()
    print(f"  news.json {'存在，' + str(len(news)) + ' 个对象有动态' if news else '不存在，使用占位'}")

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    news_json = json.dumps(news, ensure_ascii=False, separators=(",", ":"))
    meta_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))

    dynamics_api_base = (DYNAMICS_API_BASE or "").strip()
    dynamics_api_base_json = json.dumps(dynamics_api_base, ensure_ascii=False)
    if dynamics_api_base:
        print(f"实时动态接口: 已配置 {dynamics_api_base}（看板将显示“查最新”按钮）")
    else:
        print("实时动态接口: 未配置（DYNAMICS_API_BASE 为空，看板只显示快照，不显示“查最新”按钮）")

    data_kb = len(data_json.encode("utf-8")) / 1024
    print(f"  数据部分大小: {data_kb:.1f} KB")

    if echarts_mode == "inline":
        echarts_tag = "<script>\n/* ECharts (bundled offline at build time) */\n" + echarts_content + "\n</script>"
    else:
        echarts_tag = (
            f'<!-- 警告: 构建时无法内联 ECharts，回退到 CDN，本页面并非完全离线自包含 -->\n'
            f'<script src="{echarts_content}"></script>'
        )

    html = HTML_TEMPLATE
    html = html.replace("@@ECHARTS_TAG@@", echarts_tag)
    html = html.replace("@@DATA_JSON@@", data_json)
    html = html.replace("@@NEWS_JSON@@", news_json)
    html = html.replace("@@META_JSON@@", meta_json)
    html = html.replace("@@DYNAMICS_API_BASE_JSON@@", dynamics_api_base_json)
    html = html.replace(
        "@@COVERAGE_TEXT@@",
        f"数据覆盖 {meta['coverageStartYear']}年{meta['coverageStartMonth']}月 "
        f"至 {meta['coverageEndYear']}年{meta['coverageEndMonth']}月",
    )
    html = html.replace("@@UPDATE_TEXT@@", f"更新时间 {meta['buildTime']}")
    html = html.replace("@@CALIBER_SHORT@@", _esc(CALIBER_SHORT))
    html = html.replace("@@CALIBER_LONG@@", _esc(CALIBER_LONG))
    html = html.replace("@@MULTI_BRAND_DESC@@", _esc(meta["multiBrandDesc"]))
    html = html.replace(
        "@@MANU_COUNT@@", str(meta["manufacturers"])
    ).replace(
        "@@BRAND_COUNT@@", str(meta["brands"])
    ).replace(
        "@@MODEL_COUNT@@", str(meta["models"])
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"完成: {OUT_PATH} ({size_kb:.1f} KB, echarts 模式: {echarts_mode})")


# ---------------------------------------------------------------------------
# 5. HTML / CSS / JS 模板
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>中国汽车销量看板</title>
<style>
:root{
  color-scheme: light;
  /* Sardine 品牌层（浅色）：Cream 底 + 纯白卡片 + Ink 文字，Mustard 为主强调 */
  --surface-1:#ffffff;
  --page-plane:#fcf4e4;
  --text-primary:#151515;
  --text-secondary:#4a4842;
  --text-muted:#75705f;
  --grid:#e1e0d9;
  --baseline:#c3c2b7;
  --border:rgba(21,21,21,0.10);
  --good:#0ca30c;
  --critical:#d03b3b;
  --critical-solid:#d03b3b;
  --warning-bg:rgba(250,178,25,0.16);
  --warning-fg:#7a5100;
  --warning-border:rgba(250,178,25,0.55);
  --info-bg:rgba(0,134,193,0.10);
  --info-fg:#00486a;
  --info-border:rgba(0,134,193,0.35);
  /* 图表分类色板（第二层，独立推导，锚点 = Mist Blue / Mustard，已跑 dataviz 验证器） */
  --series-1:#0086c1;
  --series-2:#eb6834;
  --series-3:#1baf7a;
  --series-4:#b78600;
  --series-5:#e87ba4;
  --series-6:#008300;
  --series-7:#4a3aa7;
  --series-8:#e34948;
  --muted-line:#a6a49a;
  --other-line:#89877e;
  --chip-bg:#f3ead4;
  --chip-bg-active:#f5bb40;
  --chip-fg-active:#151515;
  --overlay:rgba(21,21,21,0.35);
  --shadow: 0 8px 30px rgba(21,21,21,0.16);
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme: dark;
    /* Sardine 品牌层（深色）：Ink 底 + 暖灰卡片 + Cream 文字，Mustard 依旧是主强调 */
    --surface-1:#1e1c18;
    --page-plane:#151515;
    --text-primary:#fcf4e4;
    --text-secondary:#c9c3b4;
    --text-muted:#9a9484;
    --grid:#2c2c2a;
    --baseline:#383835;
    --border:rgba(252,244,228,0.10);
    --good:#0ca30c;
    --critical:#e66767;
    --critical-solid:#d03b3b;
    --warning-bg:rgba(250,178,25,0.14);
    --warning-fg:#fab219;
    --warning-border:rgba(250,178,25,0.45);
    --info-bg:rgba(0,150,215,0.14);
    --info-fg:#82ceff;
    --info-border:rgba(0,150,215,0.4);
    --series-1:#0096d7;
    --series-2:#d95926;
    --series-3:#199e70;
    --series-4:#bb8800;
    --series-5:#d55181;
    --series-6:#008300;
    --series-7:#9085e9;
    --series-8:#e66767;
    --muted-line:#6b6a63;
    --other-line:#6b6a63;
    --chip-bg:#2a2721;
    --chip-bg-active:#f5bb40;
    --chip-fg-active:#151515;
    --overlay:rgba(0,0,0,0.55);
    --shadow: 0 8px 30px rgba(0,0,0,0.5);
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-1:#1e1c18;
  --page-plane:#151515;
  --text-primary:#fcf4e4;
  --text-secondary:#c9c3b4;
  --text-muted:#9a9484;
  --grid:#2c2c2a;
  --baseline:#383835;
  --border:rgba(252,244,228,0.10);
  --good:#0ca30c;
  --critical:#e66767;
  --critical-solid:#d03b3b;
  --warning-bg:rgba(250,178,25,0.14);
  --warning-fg:#fab219;
  --warning-border:rgba(250,178,25,0.45);
  --info-bg:rgba(0,150,215,0.14);
  --info-fg:#82ceff;
  --info-border:rgba(0,150,215,0.4);
  --series-1:#0096d7;
  --series-2:#d95926;
  --series-3:#199e70;
  --series-4:#bb8800;
  --series-5:#d55181;
  --series-6:#008300;
  --series-7:#9085e9;
  --series-8:#e66767;
  --muted-line:#6b6a63;
  --other-line:#6b6a63;
  --chip-bg:#2a2721;
  --chip-bg-active:#f5bb40;
  --chip-fg-active:#151515;
  --overlay:rgba(0,0,0,0.55);
  --shadow: 0 8px 30px rgba(0,0,0,0.5);
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{
  background:var(--page-plane);
  color:var(--text-primary);
  font-family: system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
  min-height:100vh;
}
.wrap{max-width:1320px;margin:0 auto;padding:12px 16px 48px;}
header.app-header{
  display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;
  gap:10px;padding:14px 4px 12px;
}
.app-title{font-size:20px;font-weight:700;letter-spacing:.2px;}
.app-sub{margin-top:4px;font-size:12.5px;color:var(--text-secondary);display:flex;gap:14px;flex-wrap:wrap;}
.app-sub span{white-space:nowrap;}
.theme-btn{
  border:1px solid var(--border);background:var(--surface-1);color:var(--text-primary);
  border-radius:999px;padding:7px 14px;font-size:13px;cursor:pointer;line-height:1;
}
.theme-btn:hover{border-color:var(--text-muted);}

.card{
  background:var(--surface-1);border:1px solid var(--border);border-radius:14px;
}
.controls{
  padding:12px 14px;margin-bottom:12px;display:flex;flex-wrap:wrap;gap:14px 22px;align-items:center;
}
.ctrl-group{display:flex;flex-direction:column;gap:6px;}
.ctrl-label{font-size:11.5px;color:var(--text-muted);}
.chip-row{display:flex;gap:6px;flex-wrap:wrap;}
.chip{
  border:1px solid var(--border);background:var(--chip-bg);color:var(--text-primary);
  border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer;user-select:none;
  transition:background .12s,color .12s;white-space:nowrap;
}
.chip:hover{border-color:var(--text-muted);}
.chip.active{background:var(--chip-bg-active);color:var(--chip-fg-active);border-color:var(--chip-bg-active);}
.chip.disabled{cursor:not-allowed;opacity:.45;pointer-events:none;}
.chip-disabled-hint{font-size:10.5px;color:var(--text-muted);}
select.bodytype-select{
  border:1px solid var(--border);background:var(--chip-bg);color:var(--text-primary);
  border-radius:8px;padding:6px 10px;font-size:13px;cursor:pointer;
}
.switch-row{display:flex;align-items:center;gap:8px;}
.switch{
  position:relative;width:42px;height:24px;border-radius:999px;background:var(--chip-bg);
  border:1px solid var(--border);cursor:pointer;flex:none;
}
.switch .knob{
  position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;
  background:var(--text-muted);transition:left .15s,background .15s;
}
.switch.on{background:var(--series-1);}
.switch.on .knob{left:20px;background:#fff;}
.switch-label{font-size:13px;color:var(--text-secondary);}

.main-grid{display:flex;gap:12px;align-items:flex-start;}
.chart-panel{flex:1 1 0;min-width:0;padding:10px 6px 6px;}
.chart-toolbar{display:flex;justify-content:space-between;align-items:center;padding:2px 8px 4px;gap:8px;flex-wrap:wrap;}
.chart-title{font-size:13.5px;color:var(--text-secondary);}
.small-btn{
  border:1px solid var(--border);background:transparent;color:var(--text-secondary);
  border-radius:7px;padding:5px 10px;font-size:12.5px;cursor:pointer;
}
.small-btn:hover{color:var(--text-primary);border-color:var(--text-muted);}
.btn-pair{display:flex;gap:8px;}
.small-btn.primary{
  background:var(--chip-bg-active);color:var(--chip-fg-active);border-color:var(--chip-bg-active);font-weight:700;
}
.small-btn.primary:hover{color:var(--chip-fg-active);filter:brightness(1.06);border-color:var(--chip-bg-active);}
.chart-stage{position:relative;}
#chart{width:100%;height:520px;}
.chart-empty-hint{
  position:absolute;inset:0;display:none;align-items:center;justify-content:center;
  padding:24px;pointer-events:none;
}
.chart-empty-hint-card{
  max-width:340px;text-align:center;font-size:13px;line-height:1.7;
  background:var(--info-bg);color:var(--info-fg);border:1px solid var(--info-border);
  border-radius:12px;padding:16px 22px;
  pointer-events:auto;display:flex;flex-direction:column;align-items:center;gap:10px;
}
.legend-empty-hint{
  text-align:center;font-size:12.5px;line-height:1.7;
  background:var(--info-bg);color:var(--info-fg);border:1px solid var(--info-border);
  border-radius:10px;padding:14px 12px;margin-top:4px;
  display:flex;flex-direction:column;align-items:center;gap:8px;
}
#tableview{display:none;max-height:520px;overflow:auto;padding:0 8px 8px;}
table.datatable{width:100%;border-collapse:collapse;font-size:12.5px;}
table.datatable th,table.datatable td{
  padding:6px 8px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap;
  font-variant-numeric:tabular-nums;
}
table.datatable th:first-child,table.datatable td:first-child{text-align:left;position:sticky;left:0;background:var(--surface-1);}
table.datatable thead th{color:var(--text-muted);font-weight:600;position:sticky;top:0;background:var(--surface-1);}

.legend-panel{
  width:290px;flex:none;padding:10px 10px 12px;display:flex;flex-direction:column;gap:8px;max-height:580px;
}
.legend-search{
  border:1px solid var(--border);background:var(--page-plane);color:var(--text-primary);
  border-radius:8px;padding:8px 10px;font-size:13px;width:100%;outline:none;
}
.legend-search:focus{border-color:var(--series-1);}
.legend-meta{font-size:11.5px;color:var(--text-muted);display:flex;justify-content:space-between;}
.other-toggle-row{display:flex;align-items:flex-start;gap:7px;font-size:11.5px;color:var(--text-secondary);
  cursor:pointer;line-height:1.4;padding:2px 1px;}
.other-toggle-row input{flex:none;margin-top:2px;accent-color:var(--other-line);width:13px;height:13px;cursor:pointer;}
.legend-list{overflow-y:auto;flex:1;display:flex;flex-direction:column;gap:2px;padding-right:2px;}
.legend-item{
  display:flex;align-items:center;gap:8px;padding:6px 6px;border-radius:7px;cursor:pointer;font-size:12.5px;
}
.legend-item:hover{background:var(--chip-bg);}
.legend-item .dot{width:10px;height:10px;border-radius:50%;flex:none;}
.legend-item .rank{width:20px;flex:none;color:var(--text-muted);font-size:11px;text-align:right;font-variant-numeric:tabular-nums;}
.legend-item .name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-primary);}
.legend-item .val{color:var(--text-secondary);font-size:11.5px;font-variant-numeric:tabular-nums;flex:none;}
.legend-item input[type=checkbox]{flex:none;accent-color:var(--series-1);width:14px;height:14px;cursor:pointer;}
.legend-item.dim .name,.legend-item.dim .val{opacity:.45;}
.legend-item.other .dot{border:1px dashed var(--other-line);background:transparent;}

.footnote{font-size:11.5px;color:var(--text-muted);line-height:1.7;padding:14px 6px 0;}

.caliber-badge{
  display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;
  background:var(--info-bg);color:var(--info-fg);border:1px solid var(--info-border);
  border-radius:999px;padding:3px 10px;cursor:help;white-space:nowrap;
}
.caliber-inline{
  display:inline-flex;align-items:center;font-size:11.5px;font-weight:600;
  background:var(--info-bg);color:var(--info-fg);border:1px solid var(--info-border);
  border-radius:6px;padding:1px 8px;
}
.about-block{margin-top:12px;padding:12px 16px;}
.about-block summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--text-primary);padding:2px 0;}
.about-block summary:hover{color:var(--series-1);}
.about-body{margin-top:10px;font-size:12.5px;color:var(--text-secondary);line-height:1.8;}
.about-body p{margin:0 0 10px;}
.about-body p:last-child{margin-bottom:0;}
.about-body b{color:var(--text-primary);}

.comp-tooltip{
  position:fixed;z-index:60;max-width:300px;pointer-events:none;
  background:var(--surface-1);border:1px solid var(--border);border-radius:10px;
  box-shadow:var(--shadow);padding:10px 12px;font-size:12px;color:var(--text-secondary);
  line-height:1.6;display:none;
}
.comp-tooltip.show{display:block;}
.comp-tooltip .ct-head{color:var(--text-primary);font-weight:600;margin-bottom:4px;}
.comp-tooltip .ct-sub{color:var(--text-muted);margin-bottom:6px;}
.comp-tooltip .ct-row{display:flex;justify-content:space-between;gap:10px;}
.comp-tooltip .ct-row .ct-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.comp-tooltip .ct-row .ct-val{flex:none;font-variant-numeric:tabular-nums;color:var(--text-primary);}
.comp-tooltip .ct-more{color:var(--series-1);margin-top:4px;}

.other-toggle-row{display:flex;align-items:flex-start;gap:8px;font-size:11.5px;color:var(--text-secondary);cursor:pointer;line-height:1.5;}
.other-toggle-row input{margin-top:2px;flex:none;accent-color:var(--series-1);}

.scope-hint{font-size:11.5px;color:var(--text-muted);margin-bottom:8px;}
.scope-subhead{font-size:12px;font-weight:600;color:var(--text-primary);margin:10px 0 4px;}
.scope-table-wrap{max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;}
table.scope-table{width:100%;border-collapse:collapse;font-size:12px;}
table.scope-table th,table.scope-table td{padding:5px 8px;text-align:right;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;white-space:nowrap;}
table.scope-table th:first-child,table.scope-table td:first-child{text-align:left;}
table.scope-table td:first-child{overflow:hidden;text-overflow:ellipsis;max-width:160px;}
table.scope-table thead th{position:sticky;top:0;background:var(--surface-1);color:var(--text-muted);font-weight:600;}
.scope-bar-cell{position:relative;}
.scope-bar{position:absolute;left:0;top:0;bottom:0;background:var(--series-1);opacity:.12;z-index:0;}
.scope-model-line{font-size:12.5px;color:var(--text-secondary);padding:4px 0;}
.scope-model-line b{color:var(--text-primary);}

/* 抽屉 */
.drawer-backdrop{
  position:fixed;inset:0;background:var(--overlay);opacity:0;pointer-events:none;
  transition:opacity .18s;z-index:40;
}
.drawer-backdrop.open{opacity:1;pointer-events:auto;}
.drawer{
  position:fixed;top:0;right:0;height:100%;width:420px;max-width:92vw;
  background:var(--surface-1);box-shadow:var(--shadow);z-index:41;
  transform:translateX(100%);transition:transform .2s ease;
  display:flex;flex-direction:column;
}
.drawer.open{transform:translateX(0);}
.drawer-head{padding:16px 18px 10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.drawer-head h2{margin:0;font-size:17px;}
.drawer-head .sub{font-size:12px;color:var(--text-muted);margin-top:4px;}
.drawer-close{border:none;background:transparent;color:var(--text-muted);font-size:20px;cursor:pointer;line-height:1;padding:4px;}
.drawer-close:hover{color:var(--text-primary);}
.drawer-body{flex:1;overflow-y:auto;padding:14px 18px 28px;}
.drawer-section{margin-bottom:22px;}
.drawer-section h3{font-size:13px;color:var(--text-muted);margin:0 0 8px;font-weight:600;letter-spacing:.2px;}
.stat-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;}
.stat-tile{flex:1;min-width:110px;background:var(--page-plane);border:1px solid var(--border);border-radius:10px;padding:9px 11px;}
.stat-tile .lbl{font-size:11px;color:var(--text-muted);}
.stat-tile .val{font-size:17px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums;}
.stat-tile .delta{font-size:11.5px;margin-top:2px;font-variant-numeric:tabular-nums;}
.delta.up{color:var(--good);}
.delta.down{color:var(--critical);}
#drawerBar{width:100%;height:180px;}
table.mtable{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px;}
table.mtable th,table.mtable td{padding:4px 6px;text-align:right;border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;}
table.mtable th:first-child,table.mtable td:first-child{text-align:left;}
.news-box{border-top:3px solid transparent;padding-top:12px;}
.news-box.lvl-brand{border-top-color:var(--series-1);}
.news-box.lvl-manu{border-top-color:var(--series-2);}
.news-box.lvl-model{border-top-color:var(--series-3);}
.news-period{font-size:11px;color:var(--text-muted);margin-bottom:10px;}
.news-group{margin-bottom:14px;}
.news-group:last-of-type{margin-bottom:0;}
.news-dim-tag{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
  font-size:11.5px;font-weight:700;color:var(--text-secondary);background:var(--chip-bg);
  border:1px solid var(--border);margin-bottom:8px;}
.news-dim-tag .dot{width:7px;height:7px;border-radius:50%;flex:none;}
.lvl-brand .news-dim-tag .dot{background:var(--series-1);}
.lvl-manu .news-dim-tag .dot{background:var(--series-2);}
.lvl-model .news-dim-tag .dot{background:var(--series-3);}
.news-card{border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:var(--surface-1);}
.news-summary{font-size:13px;font-weight:600;line-height:1.5;color:var(--text-primary);}
.news-detail{margin-top:6px;}
.news-detail summary{font-size:11.5px;color:var(--text-muted);cursor:pointer;user-select:none;}
.news-detail summary:hover{color:var(--text-secondary);}
.news-detail[open] summary{color:var(--text-secondary);}
.news-detail-body{font-size:12px;color:var(--text-secondary);line-height:1.65;margin-top:6px;}
.news-meta{font-size:11px;color:var(--text-muted);margin-top:7px;}
.news-source-link{color:var(--info-fg);text-decoration:none;font-weight:600;}
.news-source-link:hover{text-decoration:underline;}
.news-empty{font-size:12.5px;color:var(--text-muted);padding:14px 0;text-align:center;border:1px dashed var(--border);border-radius:10px;}
.news-search-note{font-size:11.5px;color:var(--text-muted);margin-top:8px;padding:9px 11px;background:var(--chip-bg);border-radius:8px;line-height:1.65;}
.news-disclaimer{font-size:10.5px;color:var(--text-muted);margin-top:14px;padding-top:10px;border-top:1px dashed var(--border);line-height:1.65;}
.news-head{display:flex;align-items:center;justify-content:space-between;gap:10px;}
.news-head h3{margin:0;}
.dyn-btn{
  border:1px solid var(--info-border);background:var(--info-bg);color:var(--info-fg);
  border-radius:7px;padding:5px 12px;font-size:12px;font-weight:700;cursor:pointer;flex:none;
  white-space:nowrap;transition:opacity .15s;
}
.dyn-btn:hover{opacity:.82;}
.dyn-btn:disabled{cursor:default;opacity:.65;}
.news-badge{font-size:11px;color:var(--text-muted);margin:8px 0 10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-height:14px;}
.news-badge .tag-snap{color:var(--text-muted);}
.news-badge .tag-live{color:var(--good);font-weight:700;}
.news-badge .tag-cached{color:var(--info-fg);font-weight:700;}
.news-badge .badge-link{color:var(--info-fg);cursor:pointer;text-decoration:underline;font-weight:600;background:none;border:none;padding:0;font-size:11px;}
.news-badge .badge-link:hover{opacity:.8;}
.dyn-loading{border:1px dashed var(--border);border-radius:10px;padding:22px 14px;text-align:center;}
.dyn-loading .spinner{width:20px;height:20px;margin:0 auto 10px;border-radius:50%;
  border:2.5px solid var(--border);border-top-color:var(--info-fg);animation:dyn-spin 0.8s linear infinite;}
@keyframes dyn-spin{to{transform:rotate(360deg);}}
.dyn-loading .phase{font-size:12.5px;color:var(--text-secondary);font-weight:600;}
.dyn-cancel{margin-top:12px;border:1px solid var(--border);background:transparent;color:var(--text-secondary);
  border-radius:7px;padding:4px 12px;font-size:11.5px;cursor:pointer;}
.dyn-cancel:hover{color:var(--text-primary);border-color:var(--text-muted);}
.dyn-error{border:1px solid var(--warning-border);background:var(--warning-bg);color:var(--warning-fg);
  border-radius:10px;padding:10px 12px;font-size:12.5px;line-height:1.6;margin-bottom:10px;}
.news-card.impact-high{border-color:var(--critical);border-width:1.5px;background:var(--warning-bg);}
.news-card .impact-tag{display:inline-block;font-size:10px;font-weight:800;color:#fff;background:var(--critical-solid);
  border-radius:5px;padding:1px 6px;margin-right:6px;vertical-align:1px;}
.news-card .dim-inline{font-size:10.5px;color:var(--text-muted);font-weight:700;margin-bottom:4px;}
.news-nosrc{color:var(--text-muted);}
.dyn-unconfigured-note{font-size:10.5px;color:var(--text-muted);margin-top:8px;}

@media (max-width: 880px){
  .main-grid{flex-direction:column;}
  .legend-panel{width:100%;max-height:320px;}
  #chart{height:380px;}
  .drawer{width:100%;max-width:100%;}
}
</style>
</head>
<body>
<div class="wrap">
  <header class="app-header">
    <div>
      <div class="app-title">中国汽车销量看板</div>
      <div class="app-sub">
        <span id="coverageText">@@COVERAGE_TEXT@@</span>
        <span id="updateText">@@UPDATE_TEXT@@</span>
        <span id="metaCounts"></span>
      </div>
    </div>
    <button class="theme-btn" id="themeBtn" type="button">🌓 切换主题</button>
  </header>

  <div class="card controls">
    <div class="ctrl-group">
      <div class="ctrl-label">年份</div>
      <div class="chip-row" id="yearChips"></div>
    </div>
    <div class="ctrl-group">
      <div class="ctrl-label">粒度</div>
      <div class="chip-row" id="granChips">
        <div class="chip" data-gran="manu">厂商</div>
        <div class="chip" data-gran="brand">品牌</div>
        <div class="chip" data-gran="model">车体类型 → 车型</div>
        <div class="chip" data-gran="energy">能源类型</div>
      </div>
    </div>
    <div class="ctrl-group" id="bodyTypeGroup" style="display:none;">
      <div class="ctrl-label">车体类型</div>
      <select class="bodytype-select" id="bodyTypeSelect"></select>
    </div>
    <div class="ctrl-group" id="ownerGroup" style="display:none;">
      <div class="ctrl-label">归属</div>
      <select class="bodytype-select" id="ownerSelect"></select>
    </div>
    <div class="ctrl-group">
      <div class="ctrl-label">能源类型</div>
      <div class="chip-row" id="energyChips">
        <div class="chip" data-energy="all">全部</div>
        <div class="chip" data-energy="fuel">燃油</div>
        <div class="chip" data-energy="ev">新能源</div>
      </div>
      <div class="chip-disabled-hint" id="energyDisabledHint" style="display:none;">已按能源类型拆分</div>
    </div>
    <div class="ctrl-group">
      <div class="ctrl-label">图表模式</div>
      <div class="switch-row">
        <span class="switch-label">独立折线</span>
        <div class="switch" id="modeSwitch"><div class="knob"></div></div>
        <span class="switch-label">堆积面积</span>
      </div>
    </div>
    <div class="ctrl-group">
      <div class="ctrl-label">&nbsp;</div>
      <div class="btn-pair">
        <button class="small-btn primary" id="resetBtn" type="button">重置为 Top 20</button>
        <button class="small-btn" id="clearBtn" type="button">清除勾选</button>
      </div>
    </div>
  </div>

  <div class="card main-grid">
    <div class="chart-panel">
      <div class="chart-toolbar">
        <div class="chart-title" id="chartTitle"></div>
        <div style="display:flex;gap:8px;">
          <button class="small-btn" id="downloadCsvBtn" type="button" style="display:none;">下载 CSV</button>
          <button class="small-btn" id="tableToggleBtn" type="button">切换为表格视图</button>
        </div>
      </div>
      <div class="chart-stage">
        <div id="chart"></div>
        <div class="chart-empty-hint" id="chartEmptyHint" style="display:none;">
          <div class="chart-empty-hint-card" id="chartEmptyHintCard">已清除全部勾选，请从右侧列表勾选要对比的对象</div>
        </div>
      </div>
      <div id="tableview"></div>
    </div>
    <div class="legend-panel">
      <input class="legend-search" id="legendSearch" type="text" placeholder="搜索品牌 / 厂商 / 车型，勾选后加入图表">
      <label class="other-toggle-row" for="otherToggle">
        <input type="checkbox" id="otherToggle">
        <span id="otherToggleLabel">显示「其他」聚合线（未展示对象之和；独立折线默认关，堆积面积默认开）</span>
      </label>
      <div class="legend-meta"><span id="legendCount"></span><span id="legendShownCount"></span></div>
      <div class="legend-list" id="legendList"></div>
    </div>
  </div>

  <div class="footnote">
    折线图：默认展示当年 Top 20（按年初至今累计销量排名），前 8 名使用可辨识色，其余以及「其他」聚合线为淡灰色，
    悬停/点击线条或图例可高亮；勾选右侧列表可手动增补关注的对象。2026 年数据仅至 7 月，后续月份不补零，折线在此处断开。
    点击任意折线或图例条目可在右侧查看该对象的月度明细、同比、统计范围与相关动态。
    悬停「厂商」「品牌」粒度的折线或图例，会额外提示该对象当前统计范围包含哪些车型；「车型」是本工具的最小统计单位，不再细分。
  </div>

  <details class="about-block card" id="aboutBlock">
    <summary>关于数据 —— 车型 / 厂商 / 品牌 三层口径说明</summary>
    <div class="about-body">
      <p><b>销量口径：</b><span class="caliber-inline">@@CALIBER_SHORT@@</span> —— @@CALIBER_LONG@@</p>
      <p><b>三层关系：</b>「车型」是本工具的最小统计单位（每一行原始数据对应一个车体类型下的一个车型）；
      「厂商」是数据源里的原始字段（如「长安汽车」「上汽大众」），同一品牌在不同厂商生产会被算作不同厂商；
      「品牌」由映射字典归并而来（解析优先级：车型→品牌 优先，其次厂商→品牌，都没有则退回厂商原值作为品牌名），
      同一品牌旗下可能横跨多个厂商，一个厂商也可能对应多个品牌——这两种情况在本工具里都按<b>车型</b>逐条拆分统计，
      不做整厂商/整品牌层面的近似归并。</p>
      <p><b>一厂多牌的情况（按当前数据统计得出，非固定列表）：</b>@@MULTI_BRAND_DESC@@</p>
      <p>当前数据共 @@MANU_COUNT@@ 个厂商、@@BRAND_COUNT@@ 个品牌、@@MODEL_COUNT@@ 个车型。
      如果你发现某个「品牌」或「厂商」下的车型构成与预期不符，可以在图表里悬停/点击该对象查看右侧抽屉的
      「统计范围」一节——那里会完整列出当前筛选条件下计入该对象的每一个车型，不做省略，方便逐条核对。</p>
    </div>
  </details>
</div>

<div class="comp-tooltip" id="compTooltip"></div>

<div class="drawer-backdrop" id="drawerBackdrop"></div>
<div class="drawer" id="drawer">
  <div class="drawer-head">
    <div>
      <h2 id="drawerTitle">--</h2>
      <div class="sub" id="drawerSub">--</div>
    </div>
    <button class="drawer-close" id="drawerClose" type="button">✕</button>
  </div>
  <div class="drawer-body">
    <div class="drawer-section">
      <div class="stat-row" id="drawerStats"></div>
    </div>
    <div class="drawer-section">
      <h3>逐月销量（当年）</h3>
      <div id="drawerBar"></div>
      <table class="mtable" id="drawerTable"></table>
    </div>
    <div class="drawer-section" id="scopeSection">
      <h3 id="scopeTitle">统计范围</h3>
      <div id="scopeBody"></div>
    </div>
    <div class="drawer-section news-box" id="newsBox">
      <div class="news-head">
        <h3 id="newsSectionTitle">相关动态</h3>
        <button class="dyn-btn" id="dynQueryBtn" type="button" style="display:none;">查最新</button>
      </div>
      <div class="news-badge" id="newsBadge"></div>
      <div id="drawerNews"></div>
    </div>
  </div>
</div>

@@ECHARTS_TAG@@
<script>
(function(){
"use strict";
var RAW = @@DATA_JSON@@;
var NEWS = @@NEWS_JSON@@;
var META = @@META_JSON@@;
var DYNAMICS_API_BASE = @@DYNAMICS_API_BASE_JSON@@;

var PALETTE = {
  light: ['#0086c1','#eb6834','#1baf7a','#b78600','#e87ba4','#008300','#4a3aa7','#e34948'],
  dark:  ['#0096d7','#d95926','#199e70','#bb8800','#d55181','#008300','#9085e9','#e66767']
};

/* ---------------- 主题 ---------------- */
function getSystemDark(){
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}
function currentTheme(){
  var t = document.documentElement.getAttribute('data-theme');
  if(t === 'dark' || t === 'light') return t;
  return getSystemDark() ? 'dark' : 'light';
}
function applyTheme(t){
  if(t){ document.documentElement.setAttribute('data-theme', t); }
  else { document.documentElement.removeAttribute('data-theme'); }
  try{ localStorage.setItem('cn-auto-dash-theme', t || ''); }catch(e){}
  renderAll();
}
(function initTheme(){
  var saved = null;
  try{ saved = localStorage.getItem('cn-auto-dash-theme'); }catch(e){}
  if(saved){ document.documentElement.setAttribute('data-theme', saved); }
})();
document.getElementById('themeBtn').addEventListener('click', function(){
  var next = currentTheme() === 'dark' ? 'light' : 'dark';
  applyTheme(next);
});
if(window.matchMedia){
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(){
    if(!document.documentElement.getAttribute('data-theme')) renderAll();
  });
}
function cssVar(name){
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* ---------------- 状态 ---------------- */
var YEARS = META.years.slice().sort();
var state = {
  year: YEARS[YEARS.length-1],
  gran: 'manu',           // manu | brand | model | energy
  bodyType: -1,            // index into RAW.bodyTypes, or -1 = 全部车体类型 (used when gran==='model')
  owner: 'all',            // 'all' | 'manu:<厂商名>' | 'brand:<品牌名>'，仅 gran==='model' 时生效，用于按归属筛选车型池
  energy: 'all',           // all | fuel | ev
  stacked: false,
  shown: new Set(),        // 当前勾选(展示)的实体 key 集合
  searchTerm: '',
  hoverKey: null,
  tableView: false,
  otherVisible: false,    // 独立折线模式默认不显示「其他」聚合线
  otherManual: false,     // 用户是否手动改过「其他」显示开关（true 时不再随模式切换自动覆盖）
  userClearedAll: false,  // 用户是否点了「清除勾选」；为 true 时不触发下方 renderAll 里的自动补 Top20
  shownIsFallback: false  // 当前 lastShownKeys 是否是 computeShownKeys() 临时回落出的 Top20（未写入 state.shown）；
                           // 用户一旦做出会改变选择的操作，必须先 materializeShown() 把它固化进 state.shown
};

/* ---------------- 数据访问辅助 ---------------- */
function ymIndex(year, month){ return (year-2024)*12 + (month-1); }
function lastMonthOfYear(year){
  var idx = Math.min(12, RAW.nMonths - (year-2024)*12);
  return Math.max(0, idx);
}

// 车型粒度（及能源类型粒度，二者共用同一套范围过滤）当前生效的车体类型 + 归属过滤，
// 返回符合条件的车型下标数组。model 粒度和 energy 粒度都必须走这同一份判断逻辑，
// 不能各写一份——否则两个粒度下"车体类型/归属"筛选的口径迟早会跑偏。
function filteredModelIndices(){
  var m = RAW.model;
  var idxs = [];
  var ownerManuIdx = -1, ownerBrandIdx = -1;
  if(state.owner && state.owner.indexOf('manu:')===0){
    ownerManuIdx = RAW.manu.n.indexOf(state.owner.slice(5));
  } else if(state.owner && state.owner.indexOf('brand:')===0){
    ownerBrandIdx = RAW.brand.n.indexOf(state.owner.slice(6));
  }
  for(var i=0;i<m.n.length;i++){
    if(state.bodyType !== -1 && RAW.modelBody[i] !== state.bodyType) continue;
    if(ownerManuIdx >= 0 && RAW.modelManu[i] !== ownerManuIdx) continue;
    if(ownerBrandIdx >= 0 && RAW.modelBrand[i] !== ownerBrandIdx) continue;
    idxs.push(i);
  }
  return idxs;
}
function currentDim(){
  if(state.gran === 'manu') return RAW.manu;
  if(state.gran === 'brand') return RAW.brand;
  if(state.gran === 'energy'){
    // 能源类型粒度：恰好两个虚拟对象「燃油」「新能源」，月度值从车型级聚合而来，
    // 且应用当前的车体类型/归属筛选（复用 filteredModelIndices，不另写一份）。
    // 「燃油」对象：f=范围内全部车型的燃油月度之和，e=全0；「新能源」对象反之。
    // 这样 monthlyValue() 在 state.gran==='energy' 时忽略 state.energy、直接返回 f+e，
    // 正好就是各自的正确值。
    var fuelSum=[], evSum=[], zeroArr=[];
    for(var t=0;t<RAW.nMonths;t++){ fuelSum.push(0); evSum.push(0); zeroArr.push(0); }
    filteredModelIndices().forEach(function(i){
      var f = RAW.model.f[i], e = RAW.model.e[i];
      for(var t2=0;t2<RAW.nMonths;t2++){
        fuelSum[t2] += (f[t2]||0);
        evSum[t2] += (e[t2]||0);
      }
    });
    return {n:['燃油','新能源'], f:[fuelSum, zeroArr.slice()], e:[zeroArr.slice(), evSum]};
  }
  // model: 按当前车体类型 + 归属（厂商/品牌）过滤；state.bodyType===-1 表示不按车体类型过滤
  var m = RAW.model;
  var names=[], fuel=[], ev=[];
  filteredModelIndices().forEach(function(i){
    names.push(m.n[i]); fuel.push(m.f[i]); ev.push(m.e[i]);
  });
  return {n:names, f:fuel, e:ev};
}
function entityKey(gran, name){
  // 车型名全局唯一（已核验：895 个车型名零冲突），key 不再需要编码车体类型
  return gran + '|' + name;
}
function parseEntityKey(key){
  var gran = key.split('|',1)[0];
  return {gran:gran, name:key.slice(gran.length+1)};
}
function monthlyValue(fuelArr, evArr, year, month){
  var idx = ymIndex(year, month);
  if(idx < 0 || idx >= RAW.nMonths) return null;
  var f = fuelArr[idx]||0, e = evArr[idx]||0;
  // 能源类型粒度下，f/e 已经是"只含燃油"或"只含新能源"的聚合数组（另一半是全0），
  // 能源筛选 chip 在这个粒度下被禁用、且不改 state.energy，因此这里必须忽略 state.energy，
  // 直接返回 f+e——否则用户之前选的「燃油」筛选会把「新能源」这条虚拟对象的数据吃掉。
  if(state.gran==='energy') return f+e;
  if(state.energy==='fuel') return f;
  if(state.energy==='ev') return e;
  return f+e;
}

/* ---------------- 统计范围 / 构成审计（厂商·品牌 -> 车型） ----------------
   modelManu[i] / modelBrand[i] 是车型 i 对应的厂商 / 品牌在 RAW.manu.n / RAW.brand.n
   里的下标，构建时已从原始数据里逐行核验过一一对应关系。这里只做聚合，不猜测。 */
function findModelIndex(name){
  for(var i=0;i<RAW.model.n.length;i++){
    if(RAW.model.n[i]===name) return i;
  }
  return -1;
}
function modelYTD(i, year){
  var lastM = lastMonthOfYear(year);
  var sum = 0;
  for(var m=1;m<=lastM;m++){
    var v = monthlyValueAt(RAW.model.f[i], RAW.model.e[i], year, m);
    if(v!=null) sum += v;
  }
  return sum;
}
// 与 monthlyValue 相同的能源过滤逻辑，但允许显式传入 year（供审计计算复用，不依赖全局 state.year）
function monthlyValueAt(fuelArr, evArr, year, month){
  var idx = ymIndex(year, month);
  if(idx < 0 || idx >= RAW.nMonths) return null;
  var f = fuelArr[idx]||0, e = evArr[idx]||0;
  if(state.gran==='energy') return f+e; // 同 monthlyValue()：能源类型粒度下忽略 state.energy
  if(state.energy==='fuel') return f;
  if(state.energy==='ev') return e;
  return f+e;
}
// 不依赖 state.energy/state.gran 的纯粹"某一列月度数组截至某年 lastM 月"求和，供能源类型粒度的
// 「统计范围」审计表使用：那里需要的是"这个车型在燃油这一列（或新能源这一列）上的 YTD"，
// 跟 monthlyValueAt() 里"忽略 state.energy"的口径是两回事，不能混用。
function modelYTDForArr(arr, year){
  var lastM = lastMonthOfYear(year);
  var sum = 0;
  for(var m=1;m<=lastM;m++){
    var idx = ymIndex(year, m);
    if(idx<0 || idx>=RAW.nMonths) continue;
    sum += (arr[idx]||0);
  }
  return sum;
}
// energyType: 'fuel' | 'ev'。返回当前车体类型/归属范围内、该能源类型下有销量的车型列表
// （按 YTD 降序），供能源类型粒度抽屉的「统计范围」表使用。与 entityModels() 同构。
function energyModels(energyType, year){
  var idxs = filteredModelIndices();
  var out = [];
  idxs.forEach(function(i){
    var arr = energyType==='fuel' ? RAW.model.f[i] : RAW.model.e[i];
    var ytd = modelYTDForArr(arr, year);
    if(ytd<=0) return;
    out.push({
      name: RAW.model.n[i],
      bodyType: RAW.bodyTypes[RAW.modelBody[i]],
      manuName: RAW.manu.n[RAW.modelManu[i]],
      brandName: RAW.brand.n[RAW.modelBrand[i]],
      ytd: ytd
    });
  });
  out.sort(function(a,b){ return b.ytd-a.ytd; });
  return out;
}
// gran: 'manu' | 'brand'；返回当前 year/energy 筛选下，归属于该厂商/品牌的全部车型（销量>0），按销量降序
function entityModels(gran, name, year){
  var idxArr = gran==='manu' ? RAW.modelManu : RAW.modelBrand;
  var nameArr = gran==='manu' ? RAW.manu.n : RAW.brand.n;
  var targetIdx = nameArr.indexOf(name);
  if(targetIdx<0) return [];
  var out = [];
  for(var i=0;i<RAW.model.n.length;i++){
    if(idxArr[i]!==targetIdx) continue;
    var ytd = modelYTD(i, year);
    if(ytd<=0) continue;
    out.push({
      name: RAW.model.n[i],
      bodyType: RAW.bodyTypes[RAW.modelBody[i]],
      manuName: RAW.manu.n[RAW.modelManu[i]],
      brandName: RAW.brand.n[RAW.modelBrand[i]],
      ytd: ytd
    });
  }
  out.sort(function(a,b){ return b.ytd-a.ytd; });
  return out;
}
// 把车型列表按所属厂商分组求和（供"品牌"粒度展示"构成厂商"）
function groupByManu(models){
  var map = {}, order = [];
  models.forEach(function(m){
    if(!map[m.manuName]){ map[m.manuName] = {name:m.manuName, ytd:0, count:0}; order.push(m.manuName); }
    map[m.manuName].ytd += m.ytd;
    map[m.manuName].count += 1;
  });
  var out = order.map(function(k){ return map[k]; });
  out.sort(function(a,b){ return b.ytd-a.ytd; });
  return out;
}
// 车型粒度：给定 (bodyType, modelName)，返回其所属厂商/品牌名，用于抽屉里的"所属厂商/所属品牌"提示
function modelOwnership(name){
  var i = findModelIndex(name);
  if(i<0) return null;
  return {manuName: RAW.manu.n[RAW.modelManu[i]], brandName: RAW.brand.n[RAW.modelBrand[i]]};
}

// 计算当前 年/粒度/能源 下所有实体的：月度值(1..12,越界为null)、YTD累计、排名
function computeUniverse(){
  var dim = currentDim();
  var lastM = lastMonthOfYear(state.year);
  var entities = [];
  for(var i=0;i<dim.n.length;i++){
    var monthly = [];
    var cum = 0, cumArr = [];
    for(var m=1;m<=12;m++){
      if(m<=lastM){
        var v = monthlyValue(dim.f[i], dim.e[i], state.year, m);
        monthly.push(v);
        cum += (v||0);
        cumArr.push(cum);
      } else {
        monthly.push(null);
        cumArr.push(null);
      }
    }
    entities.push({
      name: dim.n[i],
      key: entityKey(state.gran, dim.n[i]),
      monthly: monthly,
      cum: cumArr,
      ytd: cum
    });
  }
  // 修正1：剔除本年累计销量<=0的对象，再排序/编排名——保证"池子大小"和"名次"
  // 只统计有销量的对象，跟抽屉「统计范围」表（entityModels 已按 ytd>0 过滤）口径一致。
  entities = entities.filter(function(e){ return e.ytd > 0; });
  entities.sort(function(a,b){ return b.ytd - a.ytd; });
  entities.forEach(function(e,idx){ e.rank = idx+1; });
  return {entities:entities, lastMonth:lastM};
}

// 同期对比专用：计算某年"截至第 capMonth 月"（不是该年自己的自然可得月份）的累计与排名。
// 用于同比 / 排名对比——上年数据即使是完整年份，也只应该累计到跟当前选中年份同一个月份，
// 否则会出现"今年7个月 vs 去年12个月"的跨期比较错误。
function computeUniverseAt(year, capMonth){
  var dim = currentDim();
  var cap = Math.max(0, Math.min(12, capMonth));
  var entities = [];
  for(var i=0;i<dim.n.length;i++){
    var cum = 0;
    for(var m=1;m<=cap;m++){
      var v = monthlyValueAt(dim.f[i], dim.e[i], year, m);
      if(v!=null) cum += v;
    }
    entities.push({
      name: dim.n[i],
      key: entityKey(state.gran, dim.n[i]),
      ytd: cum
    });
  }
  // 修正1：与 computeUniverse 保持同一口径——剔除该年（截至 capMonth）累计销量<=0 的对象。
  entities = entities.filter(function(e){ return e.ytd > 0; });
  entities.sort(function(a,b){ return b.ytd - a.ytd; });
  entities.forEach(function(e,idx){ e.rank = idx+1; });
  return entities;
}

// 修正2：state.shown 是跨筛选条件持续累积的"用户意图"全集，不因筛选变化而删除任何 key
// （旧的 ensureDefaultShown 会把不在当前池子里的 key 直接删掉，导致切换车体类型/归属后
// 用户手动勾选的对象再也回不来——已废弃，改用下面这个非破坏性的交集计算）。
// 当前实际展示的集合 = state.shown ∩ 当前池子合法key；仅当这个交集为空且用户没有主动清空时，
// 才把 Top20 的 key 补进 state.shown（同时持久化，不是只在这次渲染里临时借用）。
// 所有需要"当前展示了哪些对象"的地方（折线 series、图例勾选态、已选计数、"其他"聚合线的补集
// 计算、表格视图、CSV 导出）都必须调用这个函数取得同一份交集，不能各算各的。
function computeShownKeys(universe){
  var valid = new Set(universe.entities.map(function(e){return e.key;}));
  var inter = new Set();
  state.shown.forEach(function(k){ if(valid.has(k)) inter.add(k); });
  if(inter.size===0 && !state.userClearedAll){
    // 回落：只是这一次渲染"借用"当前池子的 Top20 来展示，不代表用户选了它们——
    // 不写回 state.shown，避免污染其它筛选口径下同名 key 的交集判定（年份/能源切换尤其如此）。
    // state.shown 保持不变，只标记这次展示的是"借来的"，供 materializeShown() 在用户动手时固化。
    universe.entities.slice(0,20).forEach(function(e){ inter.add(e.key); });
    state.shownIsFallback = true;
  } else {
    state.shownIsFallback = false;
  }
  return inter;
}
// 任何会修改 state.shown 的用户交互，在改动之前都必须先调用这个函数：
// 如果当前展示的是 computeShownKeys() 临时借出的回落集合（未写入 state.shown），
// 就把它固化成 state.shown 的实际内容，这样后续的增/删操作才是在真实的用户意图集合上做修改，
// 而不是对着一个"根本不在 state.shown 里"的 key 取消勾选、看起来毫无反应。
function materializeShown(){
  if(state.shownIsFallback){
    state.shown = new Set(lastShownKeys);
    state.shownIsFallback = false;
  }
}
function resetToTop20(){
  var u = computeUniverse();
  state.shown = new Set(u.entities.slice(0,20).map(function(e){return e.key;}));
  state.userClearedAll = false;
  state.shownIsFallback = false;
}

/* ---------------- 顶部控件渲染 ---------------- */
function renderYearChips(){
  var el = document.getElementById('yearChips');
  el.innerHTML='';
  YEARS.forEach(function(y){
    var c = document.createElement('div');
    c.className = 'chip' + (y===state.year?' active':'');
    c.setAttribute('data-year', y);
    c.textContent = y + '年';
    c.addEventListener('click', function(){
      // 修正5：切换年份不再 resetToTop20()——同一组对象换一年看走势是最常见的操作，
      // 用户手动勾选的对象应当跨年份保留；若该年这些对象都没有销量（交集为空），
      // renderAll() 里的回退逻辑会自动补上该年的 Top20。
      state.year = y; renderAll();
    });
    el.appendChild(c);
  });
}
document.querySelectorAll('#granChips .chip').forEach(function(c){
  c.addEventListener('click', function(){
    state.gran = c.getAttribute('data-gran');
    var showScopeCtrls = (state.gran==='model' || state.gran==='energy');
    document.getElementById('bodyTypeGroup').style.display = showScopeCtrls ? '' : 'none';
    document.getElementById('ownerGroup').style.display = showScopeCtrls ? '' : 'none';
    // 修正2：不再无条件 resetToTop20()——切粒度后 state.shown 里没有该粒度前缀的 key，
    // renderAll() 里的交集会自然为空，自动落到 Top20；同时保留其它粒度下用户的勾选不被清空。
    // 改动1（范围限定器 vs 度量切换器/呈现开关）：粒度本身是范围限定器，切粒度代表用户明确
    // 表达"不看这个范围了"，因此把车体类型/归属/图例搜索这些同属范围限定器的控件一并归零；
    // 注意这里不调用 resetToTop20()——上面这条"交集为空才回落 Top20"的语义必须保留。
    state.bodyType = -1;
    state.owner = 'all';
    state.searchTerm = '';
    var si = document.getElementById('legendSearch');
    if(si) si.value = '';
    syncControlStates();
    renderAll();
  });
});
document.querySelectorAll('#energyChips .chip').forEach(function(c){
  c.addEventListener('click', function(){
    // 能源类型粒度下这组 chip 被禁用（见 syncControlStates()），CSS 上已经
    // pointer-events:none 挡掉了点击，这里再加一道防线，绝不修改 state.energy——
    // 用户切走能源类型粒度后，原来选的能源筛选要原样恢复生效。
    if(state.gran==='energy') return;
    state.energy = c.getAttribute('data-energy');
    // 修正5：切换能源类型同样不再 resetToTop20()，与年份/车体类型/归属保持一致的语义——
    // 已勾选且在新口径下仍有销量的对象保留，全部落空时才回落到 Top20。
    renderAll();
  });
});
var bodySelect = document.getElementById('bodyTypeSelect');
(function(){
  var optAll = document.createElement('option');
  optAll.value = -1; optAll.textContent = '全部车体类型';
  bodySelect.appendChild(optAll);
})();
RAW.bodyTypes.forEach(function(bt,i){
  var opt = document.createElement('option');
  opt.value = i; opt.textContent = bt;
  bodySelect.appendChild(opt);
});
bodySelect.addEventListener('change', function(){
  state.bodyType = parseInt(bodySelect.value,10);
  // 修正2：不再 resetToTop20()——让 renderAll() 里的"交集为空才补 Top20"逻辑决定，
  // 这样车体类型来回切换时，用户已勾选且仍在池子里的对象不会被清空重置。
  renderAll();
});
var ownerSelect = document.getElementById('ownerSelect');
(function(){
  var optAll = document.createElement('option');
  optAll.value = 'all'; optAll.textContent = '全部';
  ownerSelect.appendChild(optAll);

  var manuNames = RAW.manu.n.slice().sort(function(a,b){ return a.localeCompare(b,'zh'); });
  var ogManu = document.createElement('optgroup'); ogManu.label = '按厂商';
  manuNames.forEach(function(n){
    var opt = document.createElement('option'); opt.value = 'manu:'+n; opt.textContent = n;
    ogManu.appendChild(opt);
  });
  ownerSelect.appendChild(ogManu);

  var brandNames = RAW.brand.n.slice().sort(function(a,b){ return a.localeCompare(b,'zh'); });
  var ogBrand = document.createElement('optgroup'); ogBrand.label = '按品牌';
  brandNames.forEach(function(n){
    var opt = document.createElement('option'); opt.value = 'brand:'+n; opt.textContent = n;
    ogBrand.appendChild(opt);
  });
  ownerSelect.appendChild(ogBrand);
})();
ownerSelect.addEventListener('change', function(){
  state.owner = ownerSelect.value;
  // 修正2：同上，不再 resetToTop20()，交由 renderAll() 里的回退逻辑处理。
  renderAll();
});
document.getElementById('modeSwitch').addEventListener('click', function(){
  state.stacked = !state.stacked;
  if(!state.otherManual){
    // 未手动改过开关时，按模式套用默认值：独立折线默认关闭「其他」，堆积面积默认打开
    state.otherVisible = state.stacked;
  }
  renderAll();
});
document.getElementById('otherToggle').addEventListener('change', function(e){
  state.otherVisible = e.target.checked;
  state.otherManual = true;
  renderAll();
});
document.getElementById('resetBtn').addEventListener('click', function(){
  resetToTop20(); renderAll();
});
document.getElementById('clearBtn').addEventListener('click', function(){
  state.shown = new Set();
  state.userClearedAll = true;
  state.shownIsFallback = false;
  // 改动2：清除勾选同样是用户明确表达"不看这个范围了"，范围限定器（车体类型/归属/图例搜索）
  // 一并归零；度量切换器（年份/能源）与呈现开关不受影响。
  state.bodyType = -1;
  state.owner = 'all';
  state.searchTerm = '';
  var si = document.getElementById('legendSearch');
  if(si) si.value = '';
  syncControlStates();
  renderAll();
});
document.getElementById('legendSearch').addEventListener('input', function(e){
  state.searchTerm = e.target.value.trim();
  renderLegend(lastUniverse);
});
document.getElementById('tableToggleBtn').addEventListener('click', function(){
  state.tableView = !state.tableView;
  document.getElementById('chart').style.display = state.tableView ? 'none' : '';
  document.getElementById('tableview').style.display = state.tableView ? 'block' : 'none';
  document.getElementById('downloadCsvBtn').style.display = state.tableView ? '' : 'none';
  document.getElementById('tableToggleBtn').textContent = state.tableView ? '切换为图表视图' : '切换为表格视图';
  if(state.tableView) renderTable(lastUniverse);
  updateEmptyHint();
});
document.getElementById('downloadCsvBtn').addEventListener('click', function(){
  downloadCsv(lastUniverse);
});

function syncControlStates(){
  document.querySelectorAll('#yearChips .chip').forEach(function(c){
    c.classList.toggle('active', String(state.year)===c.getAttribute('data-year'));
  });
  document.querySelectorAll('#granChips .chip').forEach(function(c){
    c.classList.toggle('active', c.getAttribute('data-gran')===state.gran);
  });
  var energyDisabled = state.gran==='energy';
  document.querySelectorAll('#energyChips .chip').forEach(function(c){
    c.classList.toggle('active', c.getAttribute('data-energy')===state.energy);
    c.classList.toggle('disabled', energyDisabled);
  });
  var energyHint = document.getElementById('energyDisabledHint');
  if(energyHint) energyHint.style.display = energyDisabled ? '' : 'none';
  document.getElementById('modeSwitch').classList.toggle('on', state.stacked);
  document.getElementById('otherToggle').checked = state.otherVisible;
  document.getElementById('otherToggleLabel').textContent = otherToggleHintText();
  bodySelect.value = state.bodyType;
  ownerSelect.value = state.owner;
  var showScopeCtrls = (state.gran==='model' || state.gran==='energy');
  document.getElementById('bodyTypeGroup').style.display = showScopeCtrls ? '' : 'none';
  document.getElementById('ownerGroup').style.display = showScopeCtrls ? '' : 'none';
}

/* ---------------- 主图表 ---------------- */
var chart = echarts.init(document.getElementById('chart'));
window.addEventListener('resize', function(){ chart.resize(); });
var lastUniverse = null;
// 修正2：与 lastUniverse 同步维护——当前实际展示的 key 集合（state.shown ∩ 当前池子合法key，
// 必要时已补 Top20）。renderAll() 之外的直接调用（搜索框 input、下载CSV、切换表格视图）都读它，
// 保证"当前展示了哪些对象"全站只有一份口径。
var lastShownKeys = new Set();

function granLabel(){
  if(state.gran==='manu') return '厂商';
  if(state.gran==='brand') return '品牌';
  if(state.gran==='energy') return '能源类型';
  var bt = state.bodyType===-1 ? '全部车体类型' : RAW.bodyTypes[state.bodyType];
  return '车型（' + bt + '）';
}
function energyLabel(){
  return state.energy==='all' ? '全部能源' : (state.energy==='fuel' ? '燃油' : '新能源');
}
// ---- 归属筛选相关的口径标注辅助（改动5：加了车体类型/归属筛选后，"排名"“其他”等含义会变，必须标注清楚比较池，
//      否则"第1名"可能被误读成全国第1，而不是筛选范围内第1——这是数据事故级别的问题，不能省） ----
function ownerRawName(){
  if(state.owner==='all' || !state.owner) return null;
  if(state.owner.indexOf('manu:')===0) return {name: state.owner.slice(5), isBrand:false};
  if(state.owner.indexOf('brand:')===0) return {name: state.owner.slice(6), isBrand:true};
  return null;
}
function ownerLabel(){
  // 用于标题：' · 归属：一汽红旗' 或 ' · 归属：红旗（品牌）'；非车型粒度或未筛选归属时为空串
  if(state.gran!=='model') return '';
  var o = ownerRawName();
  if(!o) return '';
  return ' · 归属：' + o.name + (o.isBrand ? '（品牌）' : '');
}
function filterScopeLabelParts(){
  // 车型粒度 / 能源类型粒度下当前生效的筛选条件（车体类型 + 归属），用于给"排名"/"其他"等口径打标注
  var parts = [];
  if(state.gran==='model' || state.gran==='energy'){
    if(state.bodyType!==-1) parts.push(RAW.bodyTypes[state.bodyType]);
    var o = ownerRawName();
    if(o) parts.push(o.name + (o.isBrand ? '（品牌）' : ''));
  }
  return parts;
}
// 能源类型粒度下，归属 + 车体类型的合并标注（顺序：归属在前，车体类型在后），
// 用于图表标题与抽屉副标题；没有任何限定时返回空串。能源类型粒度下能源筛选 chip 被禁用，
// 因此不像 model 粒度那样在标题里再拼一段 energyLabel()——那会把"忽略中的筛选"误显示成生效中。
function energyScopeLabel(){
  var parts = [];
  var o = ownerRawName();
  if(o) parts.push('归属：' + o.name + (o.isBrand ? '（品牌）' : ''));
  if(state.bodyType!==-1) parts.push(RAW.bodyTypes[state.bodyType]);
  if(parts.length===0) return '';
  return ' · ' + parts.join(' · ');
}
function rankScopeLabel(){
  var parts = filterScopeLabelParts();
  if(parts.length===0) return '当前排名';
  return '当前排名（' + parts.join('、') + ' 内）';
}
function otherLineName(){
  var parts = filterScopeLabelParts();
  if(parts.length===0) return {series:'其他', table:'其他（未展示对象合计）'};
  var txt = '其他（' + parts.join('、') + ' 内未展示车型合计）';
  return {series:txt, table:txt};
}
function otherToggleHintText(){
  var parts = filterScopeLabelParts();
  if(parts.length===0) return '显示「其他」聚合线（未展示对象之和；独立折线默认关，堆积面积默认开）';
  return '显示「其他」聚合线（' + parts.join('、') + ' 内未展示车型之和；独立折线默认关，堆积面积默认开）';
}
// 改动4：范围限定器（车体类型 / 归属）当前生效值的用户可读描述，供空状态提示使用；
// 没有任何范围限定时返回 null。只在车型粒度下可能非 null——车体类型/归属仅在 gran==='model' 时生效。
function scopeLabel(){
  if(state.gran!=='model' && state.gran!=='energy') return null;
  var parts = [];
  if(state.owner!=='all' && state.owner){
    var idx = state.owner.indexOf(':');
    var name = idx>=0 ? state.owner.slice(idx+1) : state.owner;
    parts.push('归属：' + name);
  }
  if(state.bodyType!==-1){
    parts.push(RAW.bodyTypes[state.bodyType]);
  }
  if(parts.length===0) return null;
  return parts.join(' · ');
}
// 解除范围限定（车体类型 + 归属），仅此一件事——不清空图例搜索词，也不改动已勾选对象。
function clearScopeFilters(){
  state.bodyType = -1;
  state.owner = 'all';
  syncControlStates();
  renderAll();
}

// 计算「其他」= 全部实体 - 已展示实体，按月合计后再累计为 YTD；供图表与表格/导出共用同一份口径
function computeOther(universe, shownList){
  var shownKeys = new Set(shownList.map(function(e){return e.key;}));
  var otherMonthly = [0,0,0,0,0,0,0,0,0,0,0,0];
  var totalMonthly = [0,0,0,0,0,0,0,0,0,0,0,0];
  universe.entities.forEach(function(e){
    for(var m=0;m<12;m++){
      if(e.monthly[m]==null) continue;
      totalMonthly[m]+= e.monthly[m];
      if(!shownKeys.has(e.key)) otherMonthly[m]+= e.monthly[m];
    }
  });
  var hasOther = false;
  var cum=[]; var run=0; var monthly=[];
  for(var m=0;m<12;m++){
    if(m>=universe.lastMonth){ cum.push(null); monthly.push(null); continue; }
    var diff = otherMonthly[m];
    if(diff>0.4) hasOther = true;
    run += diff;
    cum.push(run);
    monthly.push(diff);
  }
  return {hasOther:hasOther, cum:cum, monthly:monthly};
}

function buildSeries(universe){
  var shownList = universe.entities.filter(function(e){ return lastShownKeys.has(e.key); });
  // 颜色分配：仅按“默认Top20排名”的前8名给主色，其余（含手动增补）一律淡灰
  var top20Keys = universe.entities.slice(0,20).map(function(e){return e.key;});
  var top8Keys = universe.entities.slice(0,8).map(function(e){return e.key;});
  var palette = PALETTE[currentTheme()];
  var mutedColor = cssVar('--muted-line');
  var otherColor = cssVar('--other-line');

  var months = [1,2,3,4,5,6,7,8,9,10,11,12].map(function(m){ return m+'月'; });
  var series = [];
  shownList.forEach(function(e){
    var top8idx = top8Keys.indexOf(e.key);
    var color = top8idx>=0 ? palette[top8idx] : mutedColor;
    series.push({
      id: e.key,
      name: e.name,
      type: 'line',
      data: e.cum,
      showSymbol:false,
      symbolSize:8,
      connectNulls:false,
      lineStyle:{width: top8idx>=0?2:1.5, color: color},
      itemStyle:{color: color},
      areaStyle: state.stacked ? {opacity:0.55, color: color} : null,
      stack: state.stacked ? 'total' : null,
      emphasis:{focus:'series', lineStyle:{width: (top8idx>=0?3:2.5)}},
      blur:{lineStyle:{opacity:0.15},areaStyle:{opacity:0.1}},
      z: top8idx>=0 ? 10-top8idx : 2
    });
  });
  // 其他 = 全部实体 - 已展示实体
  var otherInfo = computeOther(universe, shownList);
  if(otherInfo.hasOther && state.otherVisible){
    series.push({
      id:'__other__', name: otherLineName().series, type:'line', data:otherInfo.cum,
      showSymbol:false, connectNulls:false,
      lineStyle:{width:1.5,type:'dashed',color:otherColor},
      itemStyle:{color:otherColor},
      areaStyle: state.stacked ? {opacity:0.35,color:otherColor} : null,
      stack: state.stacked ? 'total' : null,
      emphasis:{focus:'series'}, blur:{lineStyle:{opacity:0.15}},
      z:1
    });
  }
  return {months:months, series:series, otherInfo:otherInfo};
}

function updateEmptyHint(){
  var hint = document.getElementById('chartEmptyHint');
  if(!hint) return;
  var show = !state.tableView && lastShownKeys.size===0;
  hint.style.display = show ? 'flex' : 'none';
  if(!show) return;
  var card = document.getElementById('chartEmptyHintCard');
  if(!card) return;
  // 改动4(a)：车型粒度下，如果当前范围限定（车体类型/归属）+ 当年 + 能源口径下池子里
  // 一个对象都没有（不是"用户清空了勾选"，是压根没有可选对象），换一句范围感知的提示，
  // 并给出"清除范围限定"按钮；否则保留原来的"已清除全部勾选"提示。
  var u = lastUniverse;
  // 修正7：加 scopeLabel() 判断——没有任何范围限定时池子却为空（理论边界情况），
  // 给一个点了什么都不会发生的"清除范围限定"按钮反而更让人困惑。
  if((state.gran==='model' || state.gran==='energy') && u && u.entities.length===0 && scopeLabel()!==null){
    var ownerName = null;
    if(state.owner!=='all' && state.owner){
      var idx = state.owner.indexOf(':');
      ownerName = idx>=0 ? state.owner.slice(idx+1) : state.owner;
    }
    // 文案自适应：只有归属限定时不硬凑车体类型，反之亦然；能源不是范围限定器，但一并
    // 说明当前能源口径，帮助用户理解为什么是空的。
    var bracketParts = [];
    if(state.bodyType!==-1) bracketParts.push(RAW.bodyTypes[state.bodyType]);
    bracketParts.push(energyLabel());
    var msg = (ownerName ? '「' + escapeHtml(ownerName) + '」在' : '') +
      '「' + escapeHtml(bracketParts.join('·')) + '」范围内当年没有车型';
    card.innerHTML = '<div>' + msg + '</div>' +
      '<button class="small-btn" id="chartClearScopeBtn" type="button">清除范围限定</button>';
    var btn = document.getElementById('chartClearScopeBtn');
    if(btn) btn.addEventListener('click', clearScopeFilters);
  } else {
    card.textContent = '已清除全部勾选，请从右侧列表勾选要对比的对象';
  }
}
function renderChart(universe){
  var built = buildSeries(universe);
  updateEmptyHint();
  var isDark = currentTheme()==='dark';
  var textColor = cssVar('--text-secondary');
  var mutedColor = cssVar('--text-muted');
  var gridColor = cssVar('--grid');
  var surface = cssVar('--surface-1');

  var option = {
    backgroundColor:'transparent',
    animationDuration:280,
    textStyle:{color:textColor, fontFamily:'inherit'},
    grid:{left:56,right:20,top:28,bottom:36,containLabel:true},
    xAxis:{
      type:'category', data: built.months, boundaryGap:false,
      axisLine:{lineStyle:{color: cssVar('--baseline')}},
      axisTick:{show:false},
      axisLabel:{color:mutedColor, fontSize:11.5}
    },
    yAxis:{
      type:'value',
      splitLine:{lineStyle:{color:gridColor}},
      axisLabel:{color:mutedColor, fontSize:11.5, formatter:function(v){return formatCompact(v);}}
    },
    tooltip:{
      trigger:'axis',
      backgroundColor: surface,
      borderColor: cssVar('--border'),
      textStyle:{color: cssVar('--text-primary'), fontSize:12.5},
      confine:true,
      order:'valueDesc',
      formatter: function(params){
        if(!params || !params.length) return '';
        var isEnergy = state.gran === 'energy';
        // 占比分母＝当前范围内（车体类型/归属筛选后）燃油+新能源两个实体在该月的累计值之和，
        // 必须取自 lastUniverse.entities（完整池子），不能只累加 tooltip 里出现的几条线——
        // 否则用户取消勾选某条线时分母会跟着变，占比就错了（见需求文档）。
        var denom = null;
        if(isEnergy && lastUniverse && lastUniverse.entities && lastUniverse.entities.length){
          var di = params[0].dataIndex;
          denom = 0;
          lastUniverse.entities.forEach(function(e){
            if(e.cum && e.cum[di]!=null) denom += e.cum[di];
          });
        }
        var header = params[0].axisValueLabel + '（' + state.year + '年 累计' +
          (isEnergy ? ' · 占比＝占范围内燃油+新能源合计' : '') + '）';
        var lines = [header];
        params.forEach(function(p){
          if(p.value==null) return;
          var pctHtml = '';
          if(isEnergy && denom){
            var pct = p.value / denom * 100;
            pctHtml = '<span style="margin-left:10px;font-weight:400;color:'+mutedColor+';">' + pct.toFixed(1) + '%</span>';
          }
          lines.push(
            '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+p.color+';margin-right:6px;"></span>'+
            escapeHtml(p.seriesName) + '：<b style="float:right;margin-left:14px;">' + formatNum(p.value) + pctHtml + '</b>'
          );
        });
        return lines.join('<br/>');
      }
    },
    legend:{show:false},
    series: built.series
  };
  chart.setOption(option, true);
}

chart.on('click', function(params){
  if(params.seriesId==='__other__'){ return; }
  if(params.seriesName){ openDrawer(params.seriesId, params.seriesName); }
});
chart.on('mouseover', {seriesIndex:'all'}, function(params){
  if(params.seriesId) highlightKey(params.seriesId);
});
chart.getZr().on('globalout', function(){ highlightKey(null); hideCompTooltip(); });
chart.getZr().on('mousemove', function(evt){
  if(state.hoverKey && state.hoverKey!=='__other__' && (state.gran==='manu' || state.gran==='brand')){
    var parsed = parseEntityKey(state.hoverKey);
    var rect = document.getElementById('chart').getBoundingClientRect();
    showCompTooltip(rect.left + evt.offsetX, rect.top + evt.offsetY, parsed.gran, parsed.name);
  } else {
    hideCompTooltip();
  }
});

function highlightKey(key){
  state.hoverKey = key;
  chart.dispatchAction({type:'downplay'});
  if(key){ chart.dispatchAction({type:'highlight', seriesId:key}); }
  renderLegendDim();
}

/* ---------------- 构成提示（悬浮小卡片，仅厂商/品牌粒度） ---------------- */
var compTooltipEl = document.getElementById('compTooltip');
function buildCompNode(gran, name){
  var frag = document.createDocumentFragment();
  var models = entityModels(gran, name, state.year);
  var head = document.createElement('div'); head.className='ct-head';
  if(models.length===0){
    head.textContent = (gran==='manu'?'厂商「':'品牌「')+name+'」';
    frag.appendChild(head);
    var subE = document.createElement('div'); subE.className='ct-sub';
    subE.textContent = '当前筛选（'+state.year+'年 · '+energyLabel()+'）下暂无销量数据';
    frag.appendChild(subE);
    return frag;
  }
  if(gran==='manu'){
    head.textContent = '厂商「'+name+'」· 包含 '+models.length+' 个车型';
    frag.appendChild(head);
  } else {
    var manuGroups = groupByManu(models);
    head.textContent = '品牌「'+name+'」· 由 '+manuGroups.length+' 个厂商、'+models.length+' 个车型构成';
    frag.appendChild(head);
    var sub = document.createElement('div'); sub.className='ct-sub';
    sub.textContent = '厂商：'+manuGroups.map(function(g){return g.name;}).join('、');
    frag.appendChild(sub);
  }
  models.slice(0,5).forEach(function(m,i){
    var row = document.createElement('div'); row.className='ct-row';
    var nm = document.createElement('span'); nm.className='ct-name'; nm.textContent=(i+1)+'. '+m.name;
    var val = document.createElement('span'); val.className='ct-val'; val.textContent=formatCompact(m.ytd);
    row.appendChild(nm); row.appendChild(val);
    frag.appendChild(row);
  });
  if(models.length>5){
    var more = document.createElement('div'); more.className='ct-more';
    more.textContent = '…等 '+models.length+' 个，点击查看全部';
    frag.appendChild(more);
  }
  return frag;
}
function showCompTooltip(clientX, clientY, gran, name){
  if(gran!=='manu' && gran!=='brand'){ hideCompTooltip(); return; }
  compTooltipEl.innerHTML='';
  compTooltipEl.appendChild(buildCompNode(gran, name));
  compTooltipEl.classList.add('show');
  positionCompTooltip(clientX, clientY);
}
function positionCompTooltip(clientX, clientY){
  var pad = 14;
  compTooltipEl.style.left='0px'; compTooltipEl.style.top='0px';
  var w = compTooltipEl.offsetWidth || 280;
  var h = compTooltipEl.offsetHeight || 100;
  var x = clientX + pad, y = clientY + pad;
  if(x + w > window.innerWidth - 8) x = Math.max(8, clientX - w - pad);
  if(y + h > window.innerHeight - 8) y = Math.max(8, window.innerHeight - h - 8);
  compTooltipEl.style.left = x+'px';
  compTooltipEl.style.top = y+'px';
}
function hideCompTooltip(){
  compTooltipEl.classList.remove('show');
}

/* ---------------- 图例面板 ---------------- */
function renderLegend(universe){
  lastUniverse = universe;
  var listEl = document.getElementById('legendList');
  listEl.innerHTML='';
  var term = state.searchTerm.toLowerCase();
  var top8Keys = universe.entities.slice(0,8).map(function(e){return e.key;});
  var palette = PALETTE[currentTheme()];
  var mutedColor = cssVar('--muted-line');

  var shownCount = lastShownKeys.size;
  var filtered = universe.entities.filter(function(e){
    if(!term) return true;
    return e.name.toLowerCase().indexOf(term) >= 0;
  });
  // 排序：已勾选优先按 rank，其余按 rank
  filtered.sort(function(a,b){ return a.rank-b.rank; });

  filtered.forEach(function(e){
    var row = document.createElement('div');
    row.className='legend-item';
    row.setAttribute('data-key', e.key);

    var cb = document.createElement('input');
    cb.type='checkbox';
    cb.checked = lastShownKeys.has(e.key);
    cb.addEventListener('change', function(){
      // 用户在图例上勾/取消勾选，是明确的选择意图——先把当前实际展示的集合（哪怕是回落出的
      // Top20）固化进 state.shown，再在它上面做增/删，否则对一个不在 state.shown 里的
      // 回落 key 取消勾选会没有效果。
      materializeShown();
      if(cb.checked) state.shown.add(e.key); else state.shown.delete(e.key);
      state.userClearedAll = false;
      renderAll();
    });
    row.appendChild(cb);

    var rankEl = document.createElement('span');
    rankEl.className='rank'; rankEl.textContent = '#'+e.rank;
    row.appendChild(rankEl);

    var dot = document.createElement('span');
    dot.className='dot';
    var top8idx = top8Keys.indexOf(e.key);
    dot.style.background = lastShownKeys.has(e.key) ? (top8idx>=0?palette[top8idx]:mutedColor) : 'transparent';
    dot.style.border = lastShownKeys.has(e.key) ? 'none' : '1px solid ' + mutedColor;
    row.appendChild(dot);

    var nameEl = document.createElement('span');
    nameEl.className='name'; nameEl.textContent = e.name;
    row.appendChild(nameEl);

    var valEl = document.createElement('span');
    valEl.className='val'; valEl.textContent = formatCompact(e.ytd);
    row.appendChild(valEl);

    row.addEventListener('mouseenter', function(){ highlightKey(e.key); });
    row.addEventListener('mousemove', function(evt){
      if(state.gran==='manu' || state.gran==='brand'){
        showCompTooltip(evt.clientX, evt.clientY, state.gran, e.name);
      }
    });
    row.addEventListener('mouseleave', function(){ highlightKey(null); hideCompTooltip(); });
    row.addEventListener('click', function(evt){
      if(evt.target===cb) return;
      hideCompTooltip();
      openDrawer(e.key, e.name);
    });

    listEl.appendChild(row);
  });

  // 改动4(b)：图例搜索无结果，且当前有生效的范围限定（scopeLabel() 非 null）时，提示用户
  // 搜索词是相对"当前范围"而言没有匹配，而不是全库没有——并给出"清除范围限定"按钮扩大池子。
  // 没有任何范围限定时（scopeLabel() 为 null）保持原有表现，不加这个按钮——没有范围可清。
  if(filtered.length===0 && state.searchTerm){
    var sl = scopeLabel();
    if(sl){
      var emptyWrap = document.createElement('div');
      emptyWrap.className = 'legend-empty-hint';
      var emptyMsg = document.createElement('div');
      emptyMsg.textContent = '当前范围（' + sl + '）内没有匹配「' + state.searchTerm + '」的对象';
      emptyWrap.appendChild(emptyMsg);
      var scopeBtn = document.createElement('button');
      scopeBtn.className = 'small-btn';
      scopeBtn.id = 'legendClearScopeBtn';
      scopeBtn.type = 'button';
      scopeBtn.textContent = '清除范围限定';
      scopeBtn.addEventListener('click', clearScopeFilters);
      emptyWrap.appendChild(scopeBtn);
      listEl.appendChild(emptyWrap);
    }
  }

  // 修正1配套：这里统计的是"当前年份有销量的对象数"（computeUniverse 已经把 ytd<=0 的对象剔除），
  // 跟 header 的"累计收录"不是同一件事，措辞里必须点明年份 + "有销量"，否则两个数对不上会显得像 bug。
  // 数字放在最前面（而不是"2026年有销量：N 个对象"这种年份先出现的写法），避免下游按"文本里第一个
  // 数字"解析数量的场景（无论是自动化测试还是人工一眼扫读）把年份误读成对象数。
  // 修正6：搜索框有内容时，只显示过滤后的条数会误导——"0 个对象（2026年有销量）"看起来
  // 像"这个池子当年没有任何对象"，而实际上池子里有 20 个、只是没有一个匹配搜索词。
  // 有搜索词时同时给出"匹配数 / 池子总数"，让这一行永远只描述它真正在描述的东西。
  document.getElementById('legendCount').textContent = state.searchTerm
    ? ('匹配 ' + filtered.length + ' 个 / 共 ' + universe.entities.length + ' 个对象（' + state.year + '年有销量）')
    : (filtered.length + ' 个对象（' + state.year + '年有销量）');
  document.getElementById('legendShownCount').textContent = '已选 ' + shownCount;
  renderLegendDim();
}
function renderLegendDim(){
  document.querySelectorAll('.legend-item').forEach(function(row){
    var k = row.getAttribute('data-key');
    if(state.hoverKey && state.hoverKey!==k){ row.classList.add('dim'); }
    else{ row.classList.remove('dim'); }
  });
}

/* ---------------- 表格视图 ---------------- */
// 表格视图与 CSV 导出共用同一份行数据，保证两者口径一致
function buildTableRows(universe){
  var shownList = universe.entities.filter(function(e){return lastShownKeys.has(e.key);});
  shownList.sort(function(a,b){return a.rank-b.rank;});
  var rows = shownList.map(function(e){
    return {rank:'#'+e.rank, name:e.name, cum:e.cum};
  });
  var otherInfo = computeOther(universe, shownList);
  if(otherInfo.hasOther && state.otherVisible){
    rows.push({rank:'', name: otherLineName().table, cum:otherInfo.cum});
  }
  return rows;
}

function renderTable(universe){
  var el = document.getElementById('tableview');
  var rows = buildTableRows(universe);
  var html = '<table class="datatable"><thead><tr><th>排名</th><th>名称</th>';
  for(var m=1;m<=12;m++) html += '<th>'+m+'月累计</th>';
  html += '</tr></thead><tbody>';
  rows.forEach(function(r){
    html += '<tr><td>'+escapeHtml(r.rank)+'</td><td>'+escapeHtml(r.name)+'</td>';
    r.cum.forEach(function(v){ html += '<td>'+(v==null?'—':formatNum(v))+'</td>'; });
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

function csvEscape(s){
  s = String(s);
  if(/[",\n]/.test(s)) return '"' + s.replace(/"/g,'""') + '"';
  return s;
}
function downloadCsv(universe){
  if(!universe) return;
  var rows = buildTableRows(universe);
  var header = ['排名','名称'];
  for(var m=1;m<=12;m++) header.push(m+'月累计销量（YTD）');
  // 修正4：去掉首行的 "# 口径：..." 注释——表头必须是第1行，否则 Excel 筛选/数据透视表会坏掉。
  // 口径上下文已经在文件名里写清楚了（下面 fname 的拼装），不需要再占用数据首行。
  var lines = [header.map(csvEscape).join(',')];
  rows.forEach(function(r){
    var line = [r.rank, r.name].concat(r.cum.map(function(v){ return v==null ? '' : Math.round(v); }));
    lines.push(line.map(csvEscape).join(','));
  });
  var csv = '\ufeff' + lines.join('\r\n'); // 前缀 BOM，Excel 打开中文不乱码
  var blob = new Blob([csv], {type:'text/csv;charset=utf-8;'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  var scopeParts = [granLabel().replace(/[\s（）()]/g,'')];
  var ownerName = ownerRawName();
  if((state.gran==='model' || state.gran==='energy') && ownerName) scopeParts.push('归属' + ownerName.name.replace(/[\s（）()]/g,''));
  var fname = '汽车销量_' + state.year + '_' + scopeParts.join('_') + '_' + energyLabel() + '.csv';
  a.href = url; a.download = fname;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function(){ URL.revokeObjectURL(url); }, 1000);
}

/* ---------------- 抽屉 ---------------- */
var drawerChart = null;
function openDrawer(key, name){
  var backdrop = document.getElementById('drawerBackdrop');
  var drawer = document.getElementById('drawer');
  backdrop.classList.add('open'); drawer.classList.add('open');
  document.getElementById('drawerTitle').textContent = name;
  // 能源类型粒度：能源筛选 chip 被禁用、与本次展示无关，副标题不拼 energyLabel()，
  // 改用 energyScopeLabel() 体现归属/车体类型这两个仍然生效的范围限定。
  var drawerSubTail = state.gran==='energy'
    ? (energyScopeLabel() + ' · ' + state.year + '年')
    : (ownerLabel() + ' · ' + state.year + '年 · ' + energyLabel());
  document.getElementById('drawerSub').textContent = granLabel() + drawerSubTail;

  var cur = findEntity(key, state.year);
  var prevYear = state.year - 1;
  // prevFull：上年的完整 12 个月原始数据，供逐月柱状图/表格做"同月对同月"展示（本身没有跨期问题）。
  var prevFull = YEARS.indexOf(prevYear)>=0 ? findEntity(key, prevYear) : null;
  var lastM = lastUniverse.lastMonth; // N = 当前选中年份实际有数据的最后一个月（2026 年是 7，2024/2025 是 12）

  // prevCapped：上年"截至同一个第 N 月"的累计与排名——YTD 同比、排名同比都必须用这个，
  // 不能直接用 prevFull.ytd / prevFull.rank（那是上年全年数据，会把「7个月 vs 12个月」这种
  // 跨期错误同比算出来）。
  var prevCapped = null;
  if(prevFull){
    var prevCappedEntities = computeUniverseAt(prevYear, lastM);
    for(var pi=0; pi<prevCappedEntities.length; pi++){
      if(prevCappedEntities[pi].key===key){ prevCapped = prevCappedEntities[pi]; break; }
    }
  }

  // 统计卡片
  var statsEl = document.getElementById('drawerStats');
  statsEl.innerHTML='';
  var ytd = cur ? cur.ytd : 0;
  var rank = cur ? cur.rank : null;
  var prevRank = prevCapped ? prevCapped.rank : null; // 同期排名（截至第 lastM 月），不是上年全年排名

  var prevYtdForDisplay = prevCapped ? prevCapped.ytd : null;
  var rankLbl = rankScopeLabel(); // 加了车体类型/归属筛选后，"排名"是筛选后的比较池内排名，标签要点明比较池，避免误读成全国排名
  statsEl.appendChild(statTile('年初至今累计（YTD）', formatNum(ytd), null, null));
  if(rank==null){
    statsEl.appendChild(statTile(rankLbl, '--', null, null));
  } else if(prevRank==null){
    statsEl.appendChild(statTile(rankLbl, '第 '+rank+' 名', '上年同期无对应数据', null));
  } else if(prevYtdForDisplay===0 && ytd>0){
    statsEl.appendChild(statTile(rankLbl, '第 '+rank+' 名', '上年同期无销量（新增对象）', 'up'));
  } else {
    var rd = rankDeltaText(prevRank, rank);
    statsEl.appendChild(statTile(rankLbl, '第 '+rank+' 名', rd.text + '（同期对比）', rd.dir));
  }
  var dynYoyRaw = null; // 同比的原始数值（供实时动态查询接口用，不做百分号/正负号格式化）
  if(prevCapped){
    var prevYtd = prevCapped.ytd;
    var curYtd = cur ? cur.ytd : 0;
    var yoyText, yoyDir;
    if(prevYtd > 0){
      var yoy = (curYtd - prevYtd) / prevYtd * 100;
      dynYoyRaw = yoy;
      yoyText = (yoy>=0?'+':'') + yoy.toFixed(1) + '%';
      yoyDir = yoy>=0 ? 'up' : 'down';
    } else if(curYtd > 0){
      yoyText = '新增'; yoyDir = 'up'; // 去年同期为 0（新车型/新品牌），不是 +Infinity / NaN
      // dynYoyRaw 保持 null：+Infinity% 对实时查询接口没有意义，不传给它
    } else {
      yoyText = '—'; yoyDir = null; // 两年同期都是 0，同比无意义
    }
    statsEl.appendChild(statTile('同比（至'+lastM+'月）', yoyText, null, yoyDir));
  } else {
    statsEl.appendChild(statTile('同比（至'+lastM+'月）', '—', '无上年同期数据', null));
  }
  // 供「查最新」实时查询接口使用的上下文——直接复用上面已经算好的 rank / dynYoyRaw，
  // 不重算（这里是唯一权威口径）。yoy_pct 四舍五入到一位小数，与展示层的 toFixed(1) 对齐。
  var dynLevel = GRAN_TO_NEWS_LEVEL[state.gran] || 'brand';
  var dynContext = {
    year: state.year,
    ytd_months: lastM,
    yoy_pct: (dynYoyRaw!=null && isFinite(dynYoyRaw)) ? Math.round(dynYoyRaw*10)/10 : null,
    rank: (rank!=null) ? rank : null
  };

  // 月度柱状图（当年 vs 去年同月，逐月对比本身就是同期对比，不受上面那个 bug 影响）
  var months = [];
  var curMonthly = [], prevMonthly = [];
  for(var m=1;m<=12;m++){
    months.push(m+'月');
    curMonthly.push(cur && cur.monthly[m-1]!=null ? cur.monthly[m-1] : null);
    prevMonthly.push(prevFull && prevFull.monthly[m-1]!=null ? prevFull.monthly[m-1] : null);
  }
  if(!drawerChart){ drawerChart = echarts.init(document.getElementById('drawerBar')); }
  var palette = PALETTE[currentTheme()];
  drawerChart.setOption({
    backgroundColor:'transparent',
    grid:{left:44,right:10,top:10,bottom:24,containLabel:true},
    textStyle:{color:cssVar('--text-secondary')},
    xAxis:{type:'category', data:months, axisLabel:{color:cssVar('--text-muted'),fontSize:10.5},
      axisLine:{lineStyle:{color:cssVar('--baseline')}}, axisTick:{show:false}},
    yAxis:{type:'value', splitLine:{lineStyle:{color:cssVar('--grid')}},
      axisLabel:{color:cssVar('--text-muted'),fontSize:10.5,formatter:function(v){return formatCompact(v);}}},
    tooltip:{trigger:'axis', backgroundColor:cssVar('--surface-1'), borderColor:cssVar('--border'),
      textStyle:{color:cssVar('--text-primary'),fontSize:12},
      formatter:function(params){
        var s = months[params[0].dataIndex];
        params.forEach(function(p){ if(p.value!=null) s += '<br/>'+escapeHtml(p.seriesName)+'：'+formatNum(p.value); });
        return s;
      }},
    legend:{show:!!prevFull, top:0, right:0, textStyle:{color:cssVar('--text-secondary'),fontSize:11}, itemWidth:12,itemHeight:8},
    series:[
      {name:state.year+'年', type:'bar', data:curMonthly, itemStyle:{color:palette[0], borderRadius:[3,3,0,0]}, barMaxWidth:18},
      prevFull?{name:prevYear+'年', type:'bar', data:prevMonthly, itemStyle:{color:cssVar('--muted-line'), borderRadius:[3,3,0,0]}, barMaxWidth:18}:null
    ].filter(Boolean)
  }, true);
  setTimeout(function(){ drawerChart.resize(); }, 50);

  // 明细表格
  var tEl = document.getElementById('drawerTable');
  var html = '<thead><tr><th>月份</th><th>'+state.year+'年销量</th>' + (prevFull?('<th>'+prevYear+'年同月</th><th>同比</th>'):'') + '</tr></thead><tbody>';
  for(var i=0;i<12;i++){
    var cv = curMonthly[i], pv = prevMonthly[i];
    html += '<tr><td>'+months[i]+'</td><td>'+(cv==null?'—':formatNum(cv))+'</td>';
    if(prevFull){
      html += '<td>'+(pv==null?'—':formatNum(pv))+'</td>';
      var yy = (cv!=null && pv) ? ((cv-pv)/pv*100) : null;
      html += '<td'+(yy!=null?(' style="color:'+(yy>=0?cssVar('--good'):cssVar('--critical'))+'"'):'')+'>'+(yy==null?'—':(yy>=0?'+':'')+yy.toFixed(1)+'%')+'</td>';
    }
    html += '</tr>';
  }
  html += '</tbody>';
  tEl.innerHTML = html;

  // 统计范围（可审计的构成明细）
  hideCompTooltip();
  renderScope(key, name);

  // 相关动态：能源类型（燃油/新能源）不是真实实体，没有"最新动态"可查——整节隐藏，
  // 不调用 initNewsForEntity（避免遗留上一个真实对象的快照/触发无意义的实时查询）。
  var newsBoxEl = document.getElementById('newsBox');
  if(state.gran==='energy'){
    if(newsBoxEl) newsBoxEl.style.display = 'none';
    dynCancelInFlight();
  } else {
    if(newsBoxEl) newsBoxEl.style.display = '';
    // 动态：快照底版 + 用户触发实时查询
    initNewsForEntity(key, name, dynLevel, dynContext);
  }
}
function pct(v,total){ return total>0 ? (v/total*100).toFixed(1)+'%' : '—'; }
function buildScopeTable(headers, rows){
  var wrap = document.createElement('div'); wrap.className='scope-table-wrap';
  var table = document.createElement('table'); table.className='scope-table';
  var thead = document.createElement('thead'); var trh=document.createElement('tr');
  headers.forEach(function(h){ var th=document.createElement('th'); th.textContent=h; trh.appendChild(th); });
  thead.appendChild(trh); table.appendChild(thead);
  var tbody = document.createElement('tbody');
  rows.forEach(function(cols){
    var tr = document.createElement('tr');
    cols.forEach(function(c){ var td=document.createElement('td'); td.textContent=c; tr.appendChild(td); });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}
function buildDrillDownBtn(gran, name){
  // 改动4：厂商/品牌抽屉 -> 一键下钻到"这些车型"的车型粒度折线图，归属筛选自动带上当前厂商/品牌
  // 能源类型抽屉（gran==='energy'）-> 同样下钻到车型粒度，但语义不同：车体类型/归属这两个
  // 范围限定器要保持不变（用户在能源粒度下已经设好的范围，下钻后应该还是那个范围），
  // 只把能源筛选切到对应类型（燃油→'fuel'，新能源→'ev'），这样看到的就是"这个范围 + 这个能源类型"的车型。
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'drillDownBtn';
  btn.className = 'small-btn primary drill-btn';
  btn.textContent = '查看这些车型的走势 →';
  btn.addEventListener('click', function(){
    closeDrawer();
    state.gran = 'model';
    if(gran==='energy'){
      state.energy = (name==='燃油') ? 'fuel' : 'ev';
    } else {
      state.bodyType = -1;
      state.owner = (gran==='manu' ? 'manu:' : 'brand:') + name;
    }
    resetToTop20();
    // 修正3：下钻前如果图例搜索框里有残留内容，下钻后这个过滤条件还在生效，
    // 会让图例看起来"0 个对象"（其实图表是对的），必须在这里一并清空。
    state.searchTerm = '';
    var searchInput = document.getElementById('legendSearch');
    if(searchInput) searchInput.value = '';
    syncControlStates();
    renderAll();
  });
  return btn;
}
function renderScope(key, name){
  var el = document.getElementById('scopeBody');
  var titleEl = document.getElementById('scopeTitle');
  el.innerHTML='';
  var filterLabel = state.year+'年 · '+energyLabel();
  var parsed = parseEntityKey(key);

  if(parsed.gran==='model'){
    titleEl.textContent = '统计范围';
    var own = modelOwnership(name);
    var line = document.createElement('div');
    line.className='scope-model-line';
    if(own){
      line.appendChild(document.createTextNode('所属厂商：'));
      var b1=document.createElement('b'); b1.textContent=own.manuName; line.appendChild(b1);
      line.appendChild(document.createTextNode('　所属品牌：'));
      var b2=document.createElement('b'); b2.textContent=own.brandName; line.appendChild(b2);
    } else {
      line.textContent = '未能定位所属厂商/品牌（数据异常）';
    }
    el.appendChild(line);
    var hint0 = document.createElement('div');
    hint0.className='scope-hint'; hint0.style.marginTop='6px';
    hint0.textContent = '车型是本工具的最小统计单位，不再向下细分——因此这里不展示"构成"，只展示它归属的厂商与品牌，方便对照。';
    el.appendChild(hint0);
    return;
  }

  if(parsed.gran==='energy'){
    // 能源类型（燃油 / 新能源）的构成明细：跟厂商/品牌抽屉那张表同构——
    // 车型 / 车体类型 / 累计销量 / 占比，不截断，这是本工具的审计传统，必须有。
    titleEl.textContent = '统计范围';
    el.appendChild(buildDrillDownBtn('energy', name));
    var energyType = (name==='燃油') ? 'fuel' : 'ev';
    var eModels = energyModels(energyType, state.year);
    var eTotalYtd = eModels.reduce(function(s,m){return s+m.ytd;},0);
    if(eModels.length===0){
      var eEmpty = document.createElement('div');
      eEmpty.className='scope-hint';
      eEmpty.textContent = '当前范围内没有「'+name+'」的车型销量数据。';
      el.appendChild(eEmpty);
      return;
    }
    var eHint = document.createElement('div');
    eHint.className='scope-hint';
    eHint.textContent = '由 '+eModels.length+' 个车型构成，合计 '+formatNum(eTotalYtd)+'（与上方 YTD 对得上就说明口径一致）';
    el.appendChild(eHint);
    el.appendChild(buildScopeTable(
      ['车型','车体类型','累计销量','占比'],
      eModels.map(function(m){ return [m.name, m.bodyType, formatNum(m.ytd), pct(m.ytd,eTotalYtd)]; })
    ));
    return;
  }

  titleEl.textContent = '统计范围（' + filterLabel + '）';
  el.appendChild(buildDrillDownBtn(parsed.gran, name));
  var models = entityModels(parsed.gran, name, state.year);
  var totalYtd = models.reduce(function(s,m){return s+m.ytd;},0);

  if(models.length===0){
    var empty = document.createElement('div');
    empty.className='scope-hint';
    empty.textContent = '当前筛选条件（'+filterLabel+'）下没有销量数据。';
    el.appendChild(empty);
    return;
  }

  if(parsed.gran==='brand'){
    var manuGroups = groupByManu(models);
    var hint = document.createElement('div');
    hint.className='scope-hint';
    hint.textContent = '由 '+manuGroups.length+' 个厂商、'+models.length+' 个车型构成，合计 '+formatNum(totalYtd)+'（与上方 YTD 对得上就说明口径一致）';
    el.appendChild(hint);

    var sub1 = document.createElement('div'); sub1.className='scope-subhead'; sub1.textContent='构成厂商（'+manuGroups.length+'）';
    el.appendChild(sub1);
    el.appendChild(buildScopeTable(
      ['厂商','车型数','累计销量','占比'],
      manuGroups.map(function(g){ return [g.name, String(g.count), formatNum(g.ytd), pct(g.ytd,totalYtd)]; })
    ));

    var sub2 = document.createElement('div'); sub2.className='scope-subhead'; sub2.textContent='全部车型（'+models.length+'，不截断）';
    el.appendChild(sub2);
    el.appendChild(buildScopeTable(
      ['车型','所属厂商','累计销量','占比'],
      models.map(function(m){ return [m.name, m.manuName, formatNum(m.ytd), pct(m.ytd,totalYtd)]; })
    ));
  } else {
    var hintM = document.createElement('div');
    hintM.className='scope-hint';
    hintM.textContent = '厂商「'+name+'」包含 '+models.length+' 个车型（不截断），合计 '+formatNum(totalYtd)+'（与上方 YTD 对得上就说明口径一致）';
    el.appendChild(hintM);
    el.appendChild(buildScopeTable(
      ['车型','车体类型','累计销量','占比'],
      models.map(function(m){ return [m.name, m.bodyType, formatNum(m.ytd), pct(m.ytd,totalYtd)]; })
    ));
  }
}
function statTile(lbl, val, deltaText, deltaDir){
  var d = document.createElement('div');
  d.className='stat-tile';
  var l = document.createElement('div'); l.className='lbl'; l.textContent=lbl;
  var v = document.createElement('div'); v.className='val'; v.textContent=val;
  d.appendChild(l); d.appendChild(v);
  if(deltaText){
    var el = document.createElement('div');
    el.className = 'delta' + (deltaDir==='up'?' up':(deltaDir==='down'?' down':''));
    el.textContent = deltaText;
    d.appendChild(el);
  }
  return d;
}
function rankDeltaText(prevRank, rank){
  var diff = prevRank - rank; // 正数 = 名次上升（排名数字变小）
  if(diff===0) return {text:'与上年持平', dir:null};
  return diff>0 ? {text:'▲ 较上年上升 '+diff+' 名', dir:'up'} : {text:'▼ 较上年下降 '+(-diff)+' 名', dir:'down'};
}
function findEntity(key, year){
  var savedYear = state.year;
  state.year = year;
  var u = computeUniverse();
  state.year = savedYear;
  for(var i=0;i<u.entities.length;i++){ if(u.entities[i].key===key) return u.entities[i]; }
  return null;
}
/* ---------------- 相关动态（分层快照 + 用户触发实时查询：品牌 / 厂商 / 车型三层维度各不相同） ----------------
   快照数据来自 news.json（构建时 AI 联网检索生成，秒开，但时效性受限于构建时间）。
   实时数据由用户点击"查最新"触发，调用 DYNAMICS_API_BASE + /api/dynamics 针对当前对象
   即时联网检索，只挑最新、最有含金量、对当前销量现状影响最大的动态，不追求维度全面。
   三层视角刻意做出区分：区块标题、维度分组、维度标签配色都随粒度变化，
   避免品牌/厂商/车型三个入口在这一区块里长得一模一样。 */
var GRAN_TO_NEWS_LEVEL = {manu:'manufacturer', brand:'brand', model:'model'};
var NEWS_LEVEL_META = {
  brand:        {section:'市场动态', cls:'lvl-brand',
    order:['品牌战略','市场表现','渠道网络','重大舆情','技术路线','价格体系']},
  manufacturer: {section:'经营动态', cls:'lvl-manu',
    order:['财务业绩','供应链','产能工厂','海外布局','组织人事']},
  model:        {section:'产品动态', cls:'lvl-model',
    order:['改款年款','召回质量','价格调整']}
};
var _newsScopeCache = null;
function newsScopeByLevel(){
  if(_newsScopeCache) return _newsScopeCache;
  var out = {brand:[], manufacturer:[], model:[]};
  for(var k in NEWS){
    if(!Object.prototype.hasOwnProperty.call(NEWS,k)) continue;
    var lv = NEWS[k] && NEWS[k].level;
    if(out[lv]) out[lv].push(k);
  }
  _newsScopeCache = out;
  return out;
}
function newsScopeText(level){
  var scope = newsScopeByLevel();
  if(level==='brand') return 'Top 30 品牌';
  var list = scope[level] || [];
  var label = level==='manufacturer' ? '厂商' : '车型';
  return list.length
    ? list.join(' / ')+' 等 '+list.length+' 个'+label+'（分层设计样例）'
    : '暂无样例覆盖的'+label;
}
function isDynConfigured(){
  return !!(DYNAMICS_API_BASE && DYNAMICS_API_BASE.trim());
}
function newsDisclaimer(){
  var d = document.createElement('div');
  d.className = 'news-disclaimer';
  d.textContent = '快照内容由 AI 于生成日期联网检索得出；实时查询为点击时即时检索。'
    +'两者均附信源链接，请以信源原文为准。内容不含销量数字（销量以本看板数据为准）。'
    +'快照当前覆盖范围：Top 30 品牌，另有少量厂商/车型作为分层设计样例；'
    +'实时查询不受此范围限制，理论上可用于任意对象。';
  return d;
}
function newsHeader(entry){
  var d = document.createElement('div');
  d.className = 'news-period';
  var parts = [];
  if(entry.period) parts.push('数据周期 '+entry.period);
  if(entry.generated_at) parts.push('生成于 '+entry.generated_at);
  d.textContent = parts.join(' · ');
  return d;
}
function newsCard(it, opts){
  opts = opts || {};
  var card = document.createElement('div');
  card.className = 'news-card' + (it.impact==='high' ? ' impact-high' : '');
  if(opts.showDim && it.dimension){
    var dimEl = document.createElement('div');
    dimEl.className = 'dim-inline';
    dimEl.textContent = it.dimension;
    card.appendChild(dimEl);
  }
  var summary = document.createElement('div');
  summary.className = 'news-summary';
  if(it.impact==='high'){
    var tag = document.createElement('span');
    tag.className = 'impact-tag';
    tag.textContent = '重要';
    summary.appendChild(tag);
  }
  summary.appendChild(document.createTextNode(it.summary || ''));
  card.appendChild(summary);
  if(it.detail){
    var det = document.createElement('details');
    det.className = 'news-detail';
    var sm = document.createElement('summary'); sm.textContent = '详情';
    det.appendChild(sm);
    var body = document.createElement('div');
    body.className = 'news-detail-body';
    body.textContent = it.detail;
    det.appendChild(body);
    card.appendChild(det);
  }
  var meta = document.createElement('div');
  meta.className = 'news-meta';
  if(it.date){ meta.appendChild(document.createTextNode(it.date)); }
  if(it.source_url){
    // 有信源链接：渲染成真正的链接
    if(it.date) meta.appendChild(document.createTextNode(' · '));
    var a = document.createElement('a');
    a.className = 'news-source-link';
    a.href = it.source_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.textContent = it.source_name || '信源';
    meta.appendChild(a);
  } else if(it.source_name){
    // source_url 为空但还有个名字（降级情况）：纯文本展示，绝不渲染成空链接
    if(it.date) meta.appendChild(document.createTextNode(' · '));
    var span1 = document.createElement('span'); span1.className = 'news-nosrc';
    span1.textContent = it.source_name;
    meta.appendChild(span1);
  } else {
    // 连名字都没有：明确标"无信源"，而不是留空让人以为漏了
    if(it.date) meta.appendChild(document.createTextNode(' · '));
    var span2 = document.createElement('span'); span2.className = 'news-nosrc';
    span2.textContent = '无信源';
    meta.appendChild(span2);
  }
  card.appendChild(meta);
  return card;
}

/* ---- 动态区块的运行态：每次 openDrawer 都会重置，用 gen 防止"抽屉已经切换到
   别的对象，但上一个对象的实时查询请求才姗姗来迟"这种情况把画面写串。 ---- */
var dynState = {
  gen: 0,
  key: null, name: null, level: null, context: null,
  entry: null,      // 该对象在 news.json 里的快照条目（可能没有）
  mode: 'idle',      // idle(展示快照) / loading / live / cached
  controller: null,  // 当前 in-flight 请求的 AbortController
  phaseTimer: null,  // 加载态阶段性文案的定时器
  timeoutTimer: null,// 60 秒前端超时定时器
  timedOut: false    // 区分"前端超时自动 abort" vs "用户手动点取消"
};

var DYN_ERROR_MSG = {
  origin_not_allowed: '当前页面来源未获授权',
  rate_limited: '查询过于频繁，请稍后再试',
  daily_quota_exceeded: '今日查询额度已用完，明天再试',
  server_misconfigured: '查询服务未配置完成',
  upstream_failed: '检索服务暂时不可用'
};
function dynFriendlyError(code, message){
  if(code === 'bad_request') return message || '请求参数有误，请稍后再试';
  if(code && DYN_ERROR_MSG[code]) return DYN_ERROR_MSG[code];
  return message || '查询失败，请稍后再试';
}

var DYN_CACHE_PREFIX = 'dyn:';
var DYN_CACHE_MAX_ENTRIES = 40; // 简单的容量保护：超过这个条数就清掉最旧的
function dynTodayStr(){
  var d = new Date();
  var p = function(n){ return String(n).padStart(2,'0'); };
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());
}
function dynCacheKey(level, entity){
  return DYN_CACHE_PREFIX + level + ':' + entity + ':' + dynTodayStr();
}
// localStorage 在隐私模式等场景下会直接抛异常——这里的原则是：
// 任何一步失败都静默降级为"不缓存"，绝不能让缓存逻辑影响主功能。
function dynCacheGet(level, entity){
  try{
    var raw = window.localStorage.getItem(dynCacheKey(level, entity));
    if(!raw) return null;
    var obj = JSON.parse(raw);
    return (obj && obj.result) ? obj.result : null;
  }catch(e){ return null; }
}
function dynCacheSet(level, entity, result){
  try{
    window.localStorage.setItem(
      dynCacheKey(level, entity),
      JSON.stringify({_cachedAt: Date.now(), result: result})
    );
    dynCachePrune();
  }catch(e){ /* 隐私模式 / 容量超限等——降级为不缓存，不影响本次结果的展示 */ }
}
function dynCachePrune(){
  try{
    var keys = [];
    for(var i=0;i<window.localStorage.length;i++){
      var k = window.localStorage.key(i);
      if(k && k.indexOf(DYN_CACHE_PREFIX)===0) keys.push(k);
    }
    if(keys.length <= DYN_CACHE_MAX_ENTRIES) return;
    var withTime = keys.map(function(k){
      var t = 0;
      try{ var o = JSON.parse(window.localStorage.getItem(k)); t = (o && o._cachedAt) || 0; }catch(e){}
      return {k:k, t:t};
    });
    withTime.sort(function(a,b){ return a.t - b.t; });
    var toRemove = withTime.length - DYN_CACHE_MAX_ENTRIES;
    for(var j=0;j<toRemove;j++){ window.localStorage.removeItem(withTime[j].k); }
  }catch(e){ /* 忽略：清理失败不影响主功能 */ }
}

function formatGenTime(iso){
  if(!iso) return '刚刚';
  try{
    var d = new Date(iso);
    if(isNaN(d.getTime())) return iso;
    var p = function(n){ return String(n).padStart(2,'0'); };
    return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
  }catch(e){ return iso; }
}
function setDynBadge(kind, opts){
  opts = opts || {};
  var b = document.getElementById('newsBadge');
  if(!b) return;
  b.innerHTML = '';
  if(kind === 'snapshot'){
    var span = document.createElement('span'); span.className = 'tag-snap';
    span.textContent = dynState.entry && dynState.entry.generated_at
      ? ('快照 · 生成于 ' + dynState.entry.generated_at)
      : '快照 · 暂无预生成内容';
    b.appendChild(span);
  } else if(kind === 'loading'){
    var s2 = document.createElement('span'); s2.className = 'tag-snap';
    s2.textContent = '正在查询最新动态…';
    b.appendChild(s2);
  } else if(kind === 'live'){
    var s3 = document.createElement('span'); s3.className = 'tag-live';
    s3.textContent = '实时 · 刚刚查询 · ' + formatGenTime(opts.generatedAt);
    b.appendChild(s3);
  } else if(kind === 'cached'){
    var s4 = document.createElement('span'); s4.className = 'tag-cached';
    s4.textContent = '实时 · 今日已查询 · ' + formatGenTime(opts.generatedAt);
    b.appendChild(s4);
    var retryBtn = document.createElement('button');
    retryBtn.type = 'button'; retryBtn.className = 'badge-link';
    retryBtn.textContent = '重新查询';
    retryBtn.addEventListener('click', function(){ handleDynClick(true); });
    b.appendChild(retryBtn);
  }
}

function renderSnapshotFooter(el){
  el.appendChild(newsDisclaimer());
  if(!isDynConfigured()){
    var n = document.createElement('div');
    n.className = 'dyn-unconfigured-note';
    n.textContent = '实时查询未配置';
    el.appendChild(n);
  }
}
function renderSnapshotView(){
  var el = document.getElementById('drawerNews');
  el.innerHTML = '';
  var level = dynState.level;
  var meta = NEWS_LEVEL_META[level];
  var entry = dynState.entry;

  if(!entry){
    var notice = document.createElement('div');
    notice.className = 'news-empty';
    notice.textContent = '本对象未纳入快照监测范围（当前覆盖 ' + newsScopeText(level) + '）'
      + (isDynConfigured() ? '，可点击"查最新"实时检索' : '');
    el.appendChild(notice);
    renderSnapshotFooter(el);
    return;
  }

  el.appendChild(newsHeader(entry));

  var items = entry.items || [];
  if(!items.length){
    var empty = document.createElement('div');
    empty.className = 'news-empty';
    empty.textContent = '暂无可靠动态';
    el.appendChild(empty);
    if(entry.search_note){
      var note = document.createElement('div');
      note.className = 'news-search-note';
      note.textContent = entry.search_note;
      el.appendChild(note);
    }
    renderSnapshotFooter(el);
    return;
  }

  // 按 dimension 分组，组顺序遵循该层专属维度词表，组内按 date 倒序
  var groups = {}, groupOrder = [];
  items.forEach(function(it){
    var d = it.dimension || '其他';
    if(!groups[d]){ groups[d] = []; groupOrder.push(d); }
    groups[d].push(it);
  });
  var order = meta.order || [];
  groupOrder.sort(function(a,b){
    var ia = order.indexOf(a); if(ia===-1) ia = 999;
    var ib = order.indexOf(b); if(ib===-1) ib = 999;
    if(ia!==ib) return ia-ib;
    return a.localeCompare(b);
  });
  groupOrder.forEach(function(dim){
    var group = groups[dim].slice().sort(function(a,b){
      return (b.date||'').localeCompare(a.date||'');
    });
    var wrap = document.createElement('div');
    wrap.className = 'news-group';
    var tag = document.createElement('div');
    tag.className = 'news-dim-tag';
    var dot = document.createElement('span'); dot.className = 'dot';
    tag.appendChild(dot);
    tag.appendChild(document.createTextNode(dim));
    wrap.appendChild(tag);
    group.forEach(function(it){ wrap.appendChild(newsCard(it)); });
    el.appendChild(wrap);
  });

  renderSnapshotFooter(el);
}

var DYN_PHASES = ['正在检索…','正在筛选…','正在核实信源…','整理结果…'];
function startPhaseCycler(el){
  var idx = 0;
  el.textContent = DYN_PHASES[0];
  return setInterval(function(){
    idx = (idx+1) % DYN_PHASES.length;
    el.textContent = DYN_PHASES[idx];
  }, 3500); // 纯文案轮播，不假装真实进度，避免营造"知道剩多久"的错觉
}
function renderLoadingView(){
  var el = document.getElementById('drawerNews');
  el.innerHTML = '';
  var box = document.createElement('div'); box.className = 'dyn-loading';
  var spin = document.createElement('div'); spin.className = 'spinner';
  var phase = document.createElement('div'); phase.className = 'phase';
  box.appendChild(spin);
  box.appendChild(phase);
  var cancelBtn = document.createElement('button');
  cancelBtn.type = 'button'; cancelBtn.className = 'dyn-cancel'; cancelBtn.textContent = '取消';
  cancelBtn.addEventListener('click', function(){ dynCancelInFlight(true); });
  box.appendChild(cancelBtn);
  el.appendChild(box);
  if(dynState.phaseTimer) clearInterval(dynState.phaseTimer);
  dynState.phaseTimer = startPhaseCycler(phase);
}

function renderLiveResults(result, opts){
  opts = opts || {};
  var el = document.getElementById('drawerNews');
  el.innerHTML = '';
  var items = (result && result.items) ? result.items.slice() : [];
  // 按 impact 排序（high 在前），同级按 date 倒序
  items.sort(function(a,b){
    var ia = a.impact==='high' ? 0 : 1;
    var ib = b.impact==='high' ? 0 : 1;
    if(ia !== ib) return ia - ib;
    return (b.date||'').localeCompare(a.date||'');
  });
  if(!items.length){
    var empty = document.createElement('div');
    empty.className = 'news-empty';
    empty.textContent = '暂无可靠动态';
    el.appendChild(empty);
  } else {
    items.forEach(function(it){ el.appendChild(newsCard(it, {showDim:true})); });
  }
  if(result && result.note){
    var note = document.createElement('div');
    note.className = 'news-search-note';
    note.textContent = result.note;
    el.appendChild(note);
  }
  el.appendChild(newsDisclaimer());
  setDynBadge(opts.cached ? 'cached' : 'live', {generatedAt: result && result.generated_at});
}

function renderErrorAndRevert(msg){
  renderSnapshotView();
  setDynBadge('snapshot');
  var el = document.getElementById('drawerNews');
  var banner = document.createElement('div');
  banner.className = 'dyn-error';
  banner.textContent = msg;
  el.insertBefore(banner, el.firstChild);
}

function resetDynButton(){
  var btn = document.getElementById('dynQueryBtn');
  if(!btn) return;
  btn.disabled = false;
  btn.textContent = '查最新';
}
// userInitiated=true：用户主动点了"取消"，需要把界面立刻带回快照视图。
// userInitiated 缺省：仅做清理（比如抽屉切到了别的对象），视图由调用方自己接管。
function dynCancelInFlight(userInitiated){
  if(dynState.phaseTimer){ clearInterval(dynState.phaseTimer); dynState.phaseTimer = null; }
  if(dynState.timeoutTimer){ clearTimeout(dynState.timeoutTimer); dynState.timeoutTimer = null; }
  if(dynState.controller){
    var c = dynState.controller;
    dynState.controller = null;
    try{ c.abort(); }catch(e){}
  }
  if(userInitiated){
    dynState.mode = 'idle';
    resetDynButton();
    renderSnapshotView();
    setDynBadge('snapshot');
  }
}

function handleDynClick(forceRefresh){
  if(dynState.mode === 'loading') return; // 按钮本身会被禁用，这里是双保险，防止重复触发
  var level = dynState.level, name = dynState.name, context = dynState.context;

  if(!forceRefresh){
    var cached = dynCacheGet(level, name);
    if(cached){
      dynState.mode = 'cached';
      renderLiveResults(cached, {cached:true});
      return;
    }
  }

  dynState.mode = 'loading';
  dynState.timedOut = false;
  var gen = dynState.gen;
  var btn = document.getElementById('dynQueryBtn');
  if(btn){ btn.disabled = true; btn.textContent = '查询中…'; }
  renderLoadingView();
  setDynBadge('loading');

  var controller = new AbortController();
  dynState.controller = controller;
  // 60 秒前端超时：真实检索要 10-30 秒，60 秒还没回来就明确告知用户，而不是让人以为卡死。
  dynState.timeoutTimer = setTimeout(function(){
    dynState.timedOut = true;
    try{ controller.abort(); }catch(e){}
  }, 60000);

  var endpoint = DYNAMICS_API_BASE.replace(/\/+$/,'') + '/api/dynamics';
  fetch(endpoint, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({entity: name, level: level, context: context}),
    signal: controller.signal
  }).then(function(resp){
    return resp.json().catch(function(){ return null; }).then(function(body){
      return {status: resp.status, ok: resp.ok, body: body};
    });
  }).then(function(res){
    if(gen !== dynState.gen) return; // 抽屉已经切到别的对象，这次响应作废，不写串画面
    if(dynState.timeoutTimer){ clearTimeout(dynState.timeoutTimer); dynState.timeoutTimer = null; }
    if(dynState.phaseTimer){ clearInterval(dynState.phaseTimer); dynState.phaseTimer = null; }
    dynState.controller = null;
    resetDynButton();
    if(res.ok && res.body){
      dynState.mode = 'live';
      dynCacheSet(level, name, res.body);
      renderLiveResults(res.body, {cached:false});
    } else {
      dynState.mode = 'idle';
      var code = res.body && res.body.error;
      var message = res.body && res.body.message;
      renderErrorAndRevert(dynFriendlyError(code, message));
    }
  }).catch(function(err){
    if(gen !== dynState.gen) return;
    if(dynState.timeoutTimer){ clearTimeout(dynState.timeoutTimer); dynState.timeoutTimer = null; }
    if(dynState.phaseTimer){ clearInterval(dynState.phaseTimer); dynState.phaseTimer = null; }
    dynState.controller = null;
    resetDynButton();
    dynState.mode = 'idle';
    if(err && err.name === 'AbortError'){
      if(dynState.timedOut){
        renderErrorAndRevert('查询超时（超过 60 秒），请稍后再试');
      }
      // 用户主动点"取消"的情况：dynCancelInFlight(true) 已经同步把界面带回快照视图了，这里不用再处理
      return;
    }
    renderErrorAndRevert('网络请求失败，请检查网络连接后重试');
  });
}

function initNewsForEntity(key, name, level, context){
  dynCancelInFlight(); // 打断上一个对象可能还在飞的请求/定时器，不做视图重置（下面马上会重置）
  dynState.gen += 1;
  dynState.key = key; dynState.name = name; dynState.level = level; dynState.context = context;
  dynState.entry = (NEWS && NEWS[name]) || null;
  dynState.mode = 'idle';

  var meta = NEWS_LEVEL_META[level];
  var box = document.getElementById('newsBox');
  box.className = 'drawer-section news-box ' + meta.cls;
  document.getElementById('newsSectionTitle').textContent = meta.section;

  var btn = document.getElementById('dynQueryBtn');
  if(btn){
    if(isDynConfigured()){
      btn.style.display = '';
      btn.disabled = false;
      btn.textContent = '查最新';
      btn.onclick = function(){ handleDynClick(false); };
    } else {
      // 未配置：不显示按钮，不发任何请求，只展示快照——不能报错，不能显示坏掉的按钮
      btn.style.display = 'none';
      btn.onclick = null;
    }
  }

  renderSnapshotView();
  setDynBadge('snapshot');
}
document.getElementById('drawerClose').addEventListener('click', closeDrawer);
document.getElementById('drawerBackdrop').addEventListener('click', closeDrawer);
function closeDrawer(){
  document.getElementById('drawerBackdrop').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
}

/* ---------------- 工具函数 ---------------- */
function formatNum(v){
  if(v==null) return '—';
  return Math.round(v).toLocaleString('zh-CN');
}
function formatCompact(v){
  if(v==null) return '';
  var av = Math.abs(v);
  if(av>=10000) return (v/10000).toFixed(1).replace(/\.0$/,'') + '万';
  return String(Math.round(v));
}
function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

/* ---------------- 总渲染入口 ---------------- */
function renderAll(){
  syncControlStates();
  var u = computeUniverse();
  lastUniverse = u;
  // 修正2：交给 computeShownKeys 统一算"当前展示集合"（交集为空且未主动清空时才补 Top20，
  // 并把补的 Top20 持久化进 state.shown），不再用 state.shown.size===0 直接整体替换。
  lastShownKeys = computeShownKeys(u);
  // 能源类型粒度下标题不拼 energyLabel()（能源筛选被禁用、与图上两条线的口径无关），
  // 改用 energyScopeLabel() 拼归属/车体类型；没有限定时自然省略，跟 model 粒度标题的拼接风格一致。
  var titleTail = state.gran==='energy' ? energyScopeLabel() : (ownerLabel() + ' · ' + energyLabel());
  document.getElementById('chartTitle').textContent =
    state.year + '年 · ' + granLabel() + titleTail + ' · 年初至今累计销量（辆）';
  renderChart(u);
  renderLegend(u);
  if(state.tableView) renderTable(u);
  // 修正1配套：header 这行统计的是全库累计收录（跨全部年份），跟下面图例的"当前年份有销量"
  // 口径不是一回事，措辞要点明"累计收录"，避免和过滤后的池子大小对不上时让人误以为是 bug。
  var counts = '累计收录 ' + META.manufacturers+' 厂商 · '+META.brands+' 品牌 · '+META.models+' 车型 · '+META.rows+' 条记录';
  document.getElementById('metaCounts').textContent = counts;
}

renderYearChips();
resetToTop20();
renderAll();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()

