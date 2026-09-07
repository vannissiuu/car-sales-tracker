#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_capmonth12_edge.py
独立边界验证脚本：验证"最新年份也完整了"这个 2027 年 1 月必然发生的边界
（capMonth===12 时，年度对比视图应该整体退化成"三年都是全年"：
 年份小图例全部写"全年"、参考线全部消失、最新年份柱顶正常封口不再画虚线开口）。

真实数据（覆盖到 2026-07）触发不了这个边界，所以本脚本自己造一份假数据：把
/tmp/norm/sales.csv 复制一份，给最新年份（2026）补齐 8-12 月（数值取自 7 月同一
(厂商,品牌,车型,车体类型,能源类型) 组合的销量，任意但合理——不改动原始文件），
再复制一份"干净"的 /tmp/p2-chart（只要 build.py 需要的 build.py/vendor，不含 tests/
docs/versions/scratchpad/.git 等，避免误改动到真正的产物或被测代码），在这份副本里用
SALES_CSV 环境变量指向假数据跑 build.py，产物只落在副本自己的 docs/index.html 里，
绝不会碰到 /tmp/p2-chart/docs/index.html。

用法:
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 verify_capmonth12_edge.py

退出码: 全部 PASS -> 0；存在 FAIL -> 1。
这个脚本不并入 79+21 条的主套件(verify_scope_filter.py)，独立运行。
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright 包，请先运行: pip install playwright --break-system-packages"
          "（不要运行 playwright install，浏览器已预装）", file=sys.stderr)
    sys.exit(2)

REPO_SRC = "/tmp/p2-chart"
REAL_SALES_CSV = "/tmp/norm/sales.csv"

# 只拷贝 build.py 构建时真正需要读的东西：build.py 本身 + vendor/echarts.min.js（离线缓存，
# 避免联网拉包）。绝不拷贝 docs/（避免任何"看起来像是在操作真实产物"的路径混淆）、
# tests/（不需要）、.git/（跟构建无关）、versions/scratchpad/screenshots（无关）。
COPY_ITEMS = ["build.py", "vendor"]


class Results:
    def __init__(self):
        self.items = []

    def record(self, id_, name, status, expected=None, actual=None, detail=None):
        self.items.append({"id": id_, "name": name, "status": status,
                            "expected": expected, "actual": actual, "detail": detail})

    def has_fail(self):
        return any(it["status"] == "FAIL" for it in self.items)

    def print_table(self):
        print()
        print("=" * 100)
        print(f"{'编号':<6}{'结果':<6}{'名称'}")
        print("=" * 100)
        for it in self.items:
            mark = {"PASS": "PASS", "FAIL": "FAIL"}[it["status"]]
            print(f"{it['id']:<6}{mark:<6}{it['name']}")
            if it["status"] != "PASS":
                if it["expected"] is not None or it["actual"] is not None:
                    print(f"        期望: {it['expected']}")
                    print(f"        实际: {it['actual']}")
                if it["detail"]:
                    print(f"        详情: {it['detail']}")
        print("=" * 100)
        total = len(self.items)
        n_pass = sum(1 for it in self.items if it["status"] == "PASS")
        n_fail = sum(1 for it in self.items if it["status"] == "FAIL")
        print(f"总计 {total}   PASS {n_pass}   FAIL {n_fail}")
        print("=" * 100)


R = Results()


def safe_run(id_, name, fn):
    try:
        fn()
    except AssertionError as e:
        R.record(id_, name, "FAIL", detail=f"断言失败: {e}")
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        R.record(id_, name, "FAIL", detail=f"用例执行异常: {e}\n{tb}")


# ============================================================
# 第 1 步：造一份"最新年份也补满 12 个月"的假 CSV
# ============================================================

def build_fake_csv(src_path, dst_path):
    """读取真实 sales.csv，找出数据里最新的 (year,month)，把该 year 尚缺的月份（8..12，
    如果本来就到 12 月则无需再造）用"该年最新那个月"每个 (厂商,品牌,车型,车体类型,能源类型)
    组合的销量原样复制过去（只改 month 字段）——数值任意但合理（沿用同一车型最近一个月
    的真实销量，不是瞎编的离谱数字），凑满这一年 1-12 月都有数据。
    返回 (fake_row_count, extended_year, extended_months)。"""
    with open(src_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fieldnames = list(rows[0].keys())

    max_year = max(int(r["year"]) for r in rows)
    months_present = sorted(set(int(r["month"]) for r in rows if int(r["year"]) == max_year))
    max_month = max(months_present)
    if max_month >= 12:
        # 真实数据已经是完整年份了（理论上不会发生，真发生了直接告诉调用方，不要装作成功）
        return len(rows), max_year, []

    template_rows = [r for r in rows if int(r["year"]) == max_year and int(r["month"]) == max_month]
    missing_months = list(range(max_month + 1, 13))

    extra = []
    for m in missing_months:
        for r in template_rows:
            nr = dict(r)
            nr["month"] = str(m)
            extra.append(nr)

    with open(dst_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        for r in extra:
            w.writerow(r)

    return len(rows) + len(extra), max_year, missing_months


# ============================================================
# 第 2 步：在"干净副本"里用假 CSV 跑 build.py，产物只落在副本自己的 docs/ 下
# ============================================================

def build_edge_product(fake_csv_path, work_dir):
    """在 work_dir（一个只含 build.py + vendor/ 的干净副本）里执行
    `SALES_CSV=<fake_csv_path> python3 build.py`，让它的相对路径 OUT_DIR=docs/ 落在
    work_dir 自己名下，不会跟 /tmp/p2-chart/docs/index.html 产生任何交集。
    返回 (ok, message, out_html_path)。"""
    for item in COPY_ITEMS:
        src = os.path.join(REPO_SRC, item)
        dst = os.path.join(work_dir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    env = dict(os.environ)
    env["SALES_CSV"] = fake_csv_path
    # 显式清掉 NEWS_JSON，避免万一环境变量残留指向别处；不存在时 build.py 会优雅降级。
    env.pop("NEWS_JSON", None)

    proc = subprocess.run(
        [sys.executable, "build.py"],
        cwd=work_dir, env=env, capture_output=True, text=True, timeout=180,
    )
    out_html = os.path.join(work_dir, "docs", "index.html")
    if proc.returncode != 0:
        return False, f"build.py 退出码={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}", out_html
    if not os.path.isfile(out_html):
        return False, f"build.py 正常退出但没有生成 {out_html}\nstdout={proc.stdout}", out_html
    return True, proc.stdout, out_html


# ============================================================
# 第 3 步：canvas 绘制捕获（跟主套件同样的手法，独立复制一份，不依赖主套件模块）
# ============================================================

_DRAW_CAPTURE_INSTALL_JS = """
() => {
  if (window.__drawPatched) return;
  window.__drawPatched = true;
  window.__draws = {bars: [], strokes: [], dashes: []};
  window.__lastRect = null;
  const proto = CanvasRenderingContext2D.prototype;
  const origRect = proto.rect;
  proto.rect = function(x, y, w, h){
    window.__lastRect = {x: x, y: y, w: w, h: h};
    return origRect.apply(this, arguments);
  };
  const origFill = proto.fill;
  proto.fill = function(){
    window.__draws.bars.push({fillStyle: this.fillStyle, globalAlpha: this.globalAlpha, rect: window.__lastRect});
    window.__lastRect = null;
    return origFill.apply(this, arguments);
  };
  const origStroke = proto.stroke;
  proto.stroke = function(){
    window.__draws.strokes.push({strokeStyle: this.strokeStyle, lineWidth: this.lineWidth, globalAlpha: this.globalAlpha});
    return origStroke.apply(this, arguments);
  };
  const origSetLineDash = proto.setLineDash;
  proto.setLineDash = function(arr){
    if (arr && arr.length) { window.__draws.dashes.push(arr.slice()); }
    return origSetLineDash.apply(this, arguments);
  };
}
"""


def install_draw_capture(page):
    page.evaluate(_DRAW_CAPTURE_INSTALL_JS)


def reset_draw_capture(page):
    page.evaluate("() => { window.__draws = {bars: [], strokes: [], dashes: []}; window.__lastRect = null; }")


def get_draws_stable(page, min_gap_ms=120, max_wait_ms=1500):
    """轮询到"连续两次读到的绘制调用总数不再增长"为止再返回，跟主套件
    get_stable_draws_and_scale() 同样的手法，避免踩到分组柱更新动画还没画完的中间帧。"""
    elapsed = 0
    prev = None
    draws = None
    while elapsed <= max_wait_ms:
        draws = page.evaluate("() => window.__draws")
        counts = (len(draws["bars"]), len(draws["strokes"]), len(draws["dashes"]))
        if counts == prev:
            return draws
        prev = counts
        page.wait_for_timeout(min_gap_ms)
        elapsed += min_gap_ms
    return draws


def is_year_view(page):
    el = page.query_selector("#viewModeBtn")
    if el is None:
        return False
    try:
        return (el.inner_text() or "").strip() == "按月累计"
    except Exception:
        return False


def enter_year_view(page):
    if not is_year_view(page):
        btn = page.query_selector("#viewModeBtn")
        if btn is not None:
            btn.click()
            page.wait_for_timeout(400)


# ============================================================
# 主流程
# ============================================================

def main():
    work_root = tempfile.mkdtemp(prefix="capmonth12_edge_")
    fake_csv_path = os.path.join(work_root, "sales_fake_full_year.csv")
    work_dir = os.path.join(work_root, "repo_copy")
    os.makedirs(work_dir, exist_ok=True)

    print(f"临时工作目录: {work_root}")

    # ---- 步骤1：造假 CSV ----
    total_rows, extended_year, missing_months = None, None, None

    def step1():
        nonlocal total_rows, extended_year, missing_months
        if not os.path.isfile(REAL_SALES_CSV):
            R.record("EDGE1", "造假CSV：把最新年份补齐到12月", "FAIL",
                      detail=f"找不到真实数据 {REAL_SALES_CSV}")
            return
        total_rows, extended_year, missing_months = build_fake_csv(REAL_SALES_CSV, fake_csv_path)
        ok = (os.path.isfile(fake_csv_path) and missing_months == [8, 9, 10, 11, 12])
        R.record("EDGE1", "造假CSV：把最新年份(2026)补齐到12月(用7月同组合销量续写8-12月)",
                 "PASS" if ok else "FAIL",
                 expected="最新年份缺失月份=[8,9,10,11,12]，成功写出假CSV",
                 actual=f"最新年份={extended_year}，补齐月份={missing_months}，"
                        f"假CSV总行数={total_rows}，文件存在={os.path.isfile(fake_csv_path)}")
    safe_run("EDGE1", "造假CSV", step1)

    if R.has_fail():
        R.print_table()
        shutil.rmtree(work_root, ignore_errors=True)
        sys.exit(1)

    # ---- 步骤2：在干净副本里构建 ----
    out_html = None

    def step2():
        nonlocal out_html
        ok, msg, out_html = build_edge_product(fake_csv_path, work_dir)
        real_untouched = True
        try:
            real_mtime_before = os.path.getmtime("/tmp/p2-chart/docs/index.html")
        except Exception:
            real_mtime_before = None
        R.record("EDGE2", "在scratchpad的干净副本里跑build.py，产物落在副本自己的docs/下",
                 "PASS" if ok else "FAIL",
                 expected="build.py 成功退出，产物写到副本docs/index.html",
                 actual=f"成功={ok}，产物路径={out_html}，构建输出片段={msg[-500:] if not ok else '(略)'}")
    safe_run("EDGE2", "构建临时产物", step2)

    if R.has_fail() or not out_html or not os.path.isfile(out_html):
        R.print_table()
        shutil.rmtree(work_root, ignore_errors=True)
        sys.exit(1)

    def step2b():
        # 保险验证：绝没有碰真实产物 —— 真实 docs/index.html 的 META.years/coverageEndMonth
        # 跟我们这份临时产物必须不同（临时产物应该是 coverageEndMonth=12，真实的是 7），
        # 且真实文件路径本身必须原封不动地还在。
        real_path = "/tmp/p2-chart/docs/index.html"
        exists = os.path.isfile(real_path)
        with open(real_path, "r", encoding="utf-8") as f:
            real_text = f.read()
        m = re.search(r'"coverageEndMonth":\s*(\d+)', real_text)
        real_end_month = int(m.group(1)) if m else None
        with open(out_html, "r", encoding="utf-8") as f:
            fake_text = f.read()
        m2 = re.search(r'"coverageEndMonth":\s*(\d+)', fake_text)
        fake_end_month = int(m2.group(1)) if m2 else None
        ok = exists and real_end_month == 7 and fake_end_month == 12
        R.record("EDGE2b", "保险验证：真实产物/docs/index.html 没有被覆盖或改动",
                 "PASS" if ok else "FAIL",
                 expected="真实产物仍存在且 coverageEndMonth=7；临时产物 coverageEndMonth=12",
                 actual=f"真实产物存在={exists}，真实coverageEndMonth={real_end_month}，"
                        f"临时产物coverageEndMonth={fake_end_month}")
    safe_run("EDGE2b", "真实产物未被覆盖", step2b)

    # ---- 步骤3：对临时产物做3项断言 ----
    console_errors, page_errors = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        try:
            page.goto("file://" + out_html, timeout=30000)
            page.wait_for_selector("#chart", timeout=15000)
            page.wait_for_timeout(600)
        except Exception as e:
            R.record("EDGE-SETUP", "打开临时产物页面", "FAIL", detail=f"page.goto 失败: {e}")
            R.print_table()
            browser.close()
            shutil.rmtree(work_root, ignore_errors=True)
            sys.exit(1)

        def edge3():
            enter_year_view(page)
            txt = page.eval_on_selector("#yearLegend", "el => el.innerText") or ""
            lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
            has_partial = any("月" in ln and "全年" not in ln for ln in lines)
            all_full_year = (len(lines) > 0) and all(ln.endswith("全年") for ln in lines)
            shot_path = os.path.join(work_root, "EDGE3_year_legend.png")
            try:
                page.screenshot(path=shot_path)
            except Exception:
                pass
            ok = all_full_year and (not has_partial)
            R.record("EDGE3", "capMonth===12边界：年份小图例里所有年份都写“全年”，不出现“1–N月”",
                     "PASS" if ok else "FAIL",
                     expected="全部形如 'YYYY 全年'，不含 '1–N月'",
                     actual=f"{lines}")
        safe_run("EDGE3", "年份图例全部'全年'", edge3)

        def edge4():
            install_draw_capture(page)
            reset_draw_capture(page)
            enter_year_view(page)  # 若已在年视图，这里不会重复触发；下面强制切一次来触发重绘
            btn = page.query_selector("#viewModeBtn")
            # 确保拿到一次"从月视图进入年视图"的全新渲染：先回月视图，再进年视图
            if is_year_view(page) and btn is not None:
                btn.click()
                page.wait_for_timeout(300)
            reset_draw_capture(page)
            if btn is not None:
                btn.click()
                page.wait_for_timeout(300)
            draws = get_draws_stable(page)
            ref_color = page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim()"
            )
            ref_lines = [s for s in draws["strokes"] if abs(s["lineWidth"] - 2.5) < 0.05
                         and s["strokeStyle"] == ref_color]
            shot_path = os.path.join(work_root, "EDGE4_no_ref_lines.png")
            try:
                page.screenshot(path=shot_path)
            except Exception:
                pass
            ok = (len(ref_lines) == 0)
            R.record("EDGE4", "capMonth===12边界：参考线全部消失（捕获不到任何lineWidth=2.5的"
                     "--text-primary描边）",
                     "PASS" if ok else "FAIL",
                     expected="0",
                     actual=f"{len(ref_lines)}（全部stroke样式集合={sorted(set((s['strokeStyle'], round(s['lineWidth'],2)) for s in draws['strokes']))}）")
        safe_run("EDGE4", "参考线全部消失", edge4)

        def edge5():
            install_draw_capture(page)
            reset_draw_capture(page)
            btn = page.query_selector("#viewModeBtn")
            if is_year_view(page) and btn is not None:
                btn.click()
                page.wait_for_timeout(300)
            reset_draw_capture(page)
            if btn is not None:
                btn.click()
                page.wait_for_timeout(300)
            draws = get_draws_stable(page)
            shot_path = os.path.join(work_root, "EDGE5_no_dashed_top.png")
            try:
                page.screenshot(path=shot_path)
            except Exception:
                pass
            ok = (len(draws["dashes"]) == 0)
            R.record("EDGE5", "capMonth===12边界：最新年份柱顶正常封口（捕获不到任何"
                     "lineDash描边，即没有虚线开口）",
                     "PASS" if ok else "FAIL",
                     expected="0",
                     actual=f"{len(draws['dashes'])}（捕获到的dash样式={draws['dashes'][:5]}）")
        safe_run("EDGE5", "最新年份柱顶正常封口", edge5)

        def edge_no_errors():
            ok = (len(console_errors) == 0 and len(page_errors) == 0)
            R.record("EDGE6", "临时产物页面加载+切年视图过程中无console/page error",
                     "PASS" if ok else "FAIL",
                     expected="console error=0, page error=0",
                     actual=f"console error={len(console_errors)}, page error={len(page_errors)}",
                     detail=None if ok else "; ".join(str(m) for m in (console_errors + page_errors))[:800])
        safe_run("EDGE6", "无报错", edge_no_errors)

        browser.close()

    R.print_table()

    # ---- 步骤4：清理临时文件 ----
    shutil.rmtree(work_root, ignore_errors=True)
    print(f"已清理临时目录: {work_root}")

    sys.exit(1 if R.has_fail() else 0)


if __name__ == "__main__":
    main()
