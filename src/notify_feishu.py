#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify_feishu.py —— 数据同步完成后，把结果推送到飞书群（自定义机器人 webhook）。

这是"锦上添花"的一步：主线是 sales.csv 落库，本脚本的任何失败都不应该
让 GitHub Actions workflow 失败。所有异常都要兜住、打日志、exit(0)。

用法（GitHub Actions 里）：
    FEISHU_WEBHOOK=... FEISHU_SECRET=... DASHBOARD_URL=... python3 notify_feishu.py

本地/CI 里想看生成的卡片但不想真的发请求，设置：
    FEISHU_DRY_RUN=1
即可只打印卡片 JSON 和渲染出的文字，不发起任何网络请求。

卡片 JSON 结构与加签算法的依据见文末 "参考来源" 注释——均查证于飞书开放平台
官方文档及其官方内容页，不是凭记忆编的。
"""

import base64
import csv
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

# ---------------------------------------------------------------------------
# 路径 & 环境变量（都支持用环境变量覆盖，方便测试；生产环境用默认值即可）
# ---------------------------------------------------------------------------

SALES_CSV_PATH = os.environ.get("SALES_CSV_PATH", "data/sales.csv")
SYNC_REPORT_PATH = os.environ.get("SYNC_REPORT_PATH", "data/sync_report.md")

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "").strip()
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "").strip()
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").strip()
DRY_RUN = os.environ.get("FEISHU_DRY_RUN", "").strip() in ("1", "true", "True", "yes")

# --- 同步步骤真实结果（由 workflow 传入，绝不能只靠读 sync_report.md 的内容来判断
# 本次同步是否成功——报告文件随时可能是上一次成功运行遗留下来的旧文件）---
# SYNC_OUTCOME: steps.run_sync.outcome，success / failure / cancelled / skipped。
#   注意不 strip 成默认值以外的东西：允许为空字符串，代表调用方没有传（比如本地手动跑），
#   这时候不应该被误判成"同步失败"。
SYNC_OUTCOME = os.environ.get("SYNC_OUTCOME", "").strip()
# SYNC_NOTEWORTHY: sync.py 自己在成功跑完时写出的 step output。
#   三种取值都有意义，不能互相当默认值用：
#     'true'  -> 本次确实抓到新数据，或者有失败/放弃/拦截，值得处理
#     'false' -> 幂等空跑，什么都没发生，不该产生通知噪音
#     ''(空)  -> sync.py 压根没跑到写这个 output 的那一步就已经异常退出了
SYNC_NOTEWORTHY = os.environ.get("SYNC_NOTEWORTHY", "")
# GITHUB_RUN_ID: 本次 Actions 运行的编号，用来和 sync_report.md 里记录的编号比对，
# 确认报告真的是本次运行生成的，不是上一次成功运行遗留下来的陈旧文件。
# 本地测试时这个变量通常不存在，此时新鲜度检查会被跳过（不误报）。
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "").strip()
# GITHUB_RUN_URL: workflow 直接拼好传进来的运行链接，优先于下面 run_url_fallback() 里
# 自己拼接的逻辑；没传时（比如本地测试）回退到旧的拼接方式。
GITHUB_RUN_URL_OVERRIDE = os.environ.get("GITHUB_RUN_URL", "").strip()

BODY_TYPES = {"轿车", "SUV", "MPV", "运动汽车"}  # 有效车身类型；"其他" 表示尚未归类


def log(msg):
    print(f"[notify_feishu] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 数据读取 & 统计
# ---------------------------------------------------------------------------

def load_sales(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["year"] = int(row["year"])
            row["month"] = int(row["month"])
            row["sales"] = int(row["sales"])
            rows.append(row)
    return rows


def prev_ym(y, m):
    return (y, m - 1) if m > 1 else (y - 1, 12)


def month_totals(rows, y, m):
    """返回 (总销量 or None, 新能源销量, 品牌销量 dict)。当月无数据时总销量为 None。"""
    cur = [r for r in rows if r["year"] == y and r["month"] == m]
    if not cur:
        return None, 0, {}
    total = sum(r["sales"] for r in cur)
    nev = sum(r["sales"] for r in cur if r["energy_type"] == "新能源")
    brand_sales = defaultdict(int)
    for r in cur:
        brand_sales[r["brand"]] += r["sales"]
    return (total if total > 0 else None), nev, brand_sales


def pct_change(cur, prev):
    """环比/同比百分比变化。任一侧缺失或分母为 0 时返回 None（边界：不显示，而不是报错/显示0%）。"""
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / prev * 100.0


def new_unclassified_models(rows, y, m):
    """当月新出现（历史上从未出现过）且车身类型仍是"其他"（未归类）的车型名，去重排序。"""
    seen_before = set()
    for r in rows:
        if (r["year"], r["month"]) < (y, m):
            seen_before.add((r["manufacturer"], r["model"]))
    found = []
    seen_this_month = set()
    for r in rows:
        if r["year"] == y and r["month"] == m and r.get("body_type") == "其他":
            key = (r["manufacturer"], r["model"])
            if key not in seen_before and r["model"] not in seen_this_month:
                seen_this_month.add(r["model"])
                found.append(r["model"])
    return sorted(found)


def compute_month_stats(rows, y, m):
    total, nev, brand_sales = month_totals(rows, y, m)
    py, pm = prev_ym(y, m)
    prev_total, prev_nev, _ = month_totals(rows, py, pm)
    ly_total, _, _ = month_totals(rows, y - 1, m)

    nev_rate = (nev / total * 100.0) if total else None
    prev_nev_rate = (prev_nev / prev_total * 100.0) if prev_total else None
    nev_rate_pp_change = (nev_rate - prev_nev_rate) if (nev_rate is not None and prev_nev_rate is not None) else None

    top5 = sorted(brand_sales.items(), key=lambda kv: -kv[1])[:5] if brand_sales else []

    return {
        "year": y,
        "month": m,
        "total": total,
        "mom_pct": pct_change(total, prev_total),
        "yoy_pct": pct_change(total, ly_total),
        "nev_rate": nev_rate,
        "nev_rate_pp_change": nev_rate_pp_change,
        "top5": top5,
        "new_unclassified": new_unclassified_models(rows, y, m),
    }


# ---------------------------------------------------------------------------
# 同步报告解析（决定成功 / 告警，以及告警原因）
# ---------------------------------------------------------------------------

def parse_sync_report(text):
    info = {
        "blocked": False,
        "abandoned": [],       # [(month_str "2026-08", reason), ...]
        "quality_gate_fail": None,
        "run_time_utc": None,
    }

    m = re.search(r"是否被拦截[:：]\s*(是|否)", text)
    if m:
        info["blocked"] = m.group(1) == "是"

    m = re.search(r"运行时间\s*\(UTC\)[:：]\s*([0-9T:\-+.]+)", text)
    if m:
        info["run_time_utc"] = m.group(1)

    sec = re.search(r"本次运行放弃的月份\s*\((\d+)\s*个\)(.*?)(?=\n##|\Z)", text, re.S)
    if sec and int(sec.group(1)) > 0:
        for line in sec.group(2).splitlines():
            line = line.strip()
            mm = re.match(r"-\s*(\d{4}-\d{2})\s*[:：]\s*(.+)", line)
            if mm:
                info["abandoned"].append((mm.group(1), mm.group(2).strip()))

    for line in text.splitlines():
        if "守门" in line and ("未通过" in line or "失败" in line):
            info["quality_gate_fail"] = line.strip().lstrip("-").strip()
            break

    return info


def determine_target_month(report_info, latest_csv_ym):
    """本次运行"本应"更新到的月份：优先用放弃月份，其次用运行时间戳的年月，最后回退到已有数据的最新月份。"""
    if report_info["abandoned"]:
        y, m = report_info["abandoned"][0][0].split("-")
        return int(y), int(m)
    if report_info["run_time_utc"]:
        try:
            date_part = re.match(r"(\d{4})-(\d{2})-(\d{2})", report_info["run_time_utc"])
            if date_part:
                return int(date_part.group(1)), int(date_part.group(2))
        except Exception:
            pass
    return latest_csv_ym


def decide_alert(report_info, latest_csv_ym, latest_total):
    """返回 (is_alert: bool, reason: str or None)。"""
    if report_info["quality_gate_fail"]:
        return True, report_info["quality_gate_fail"]
    if report_info["blocked"]:
        return True, "抓取请求被平台风控拦截"
    if report_info["abandoned"]:
        parts = []
        for mth, reason in report_info["abandoned"]:
            if len(report_info["abandoned"]) > 1:
                parts.append(f"{mth}: {reason}")
            else:
                parts.append(reason)
        return True, "；".join(parts)
    if latest_csv_ym is None:
        return True, "sales.csv 中没有可用数据"
    if latest_total is None:
        y, m = latest_csv_ym
        return True, f"{y}年{m}月 销量数据总量为 0 或缺失，可能是抓取异常"
    return False, None


# ---------------------------------------------------------------------------
# 卡片构建（飞书交互式卡片 JSON；结构见文末"参考来源"）
# ---------------------------------------------------------------------------

def fmt_wan(n):
    return f"{n / 10000:.1f}万"


def fmt_pct_signed(v):
    return f"{v:+.1f}%"


def fmt_pp_signed(v):
    return f"{v:+.1f}pct"


def run_url_fallback():
    if GITHUB_RUN_URL_OVERRIDE:
        return GITHUB_RUN_URL_OVERRIDE
    server = os.environ.get("GITHUB_SERVER_URL", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return DASHBOARD_URL or "https://github.com"


def build_generic_alert_card(title, reason, log_url):
    """
    不依赖 sales.csv / sync_report.md 内容的通用告警卡片——用于"同步步骤本身就没
    成功"或"新鲜度校验没通过"这类场景：这时候报告文件的内容完全不可信（可能是
    上一次成功运行遗留的），绝不能去读它、更不能把它的内容当成本次状态展示出来。
    """
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "red"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**原因**：{reason}"}},
            {
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看运行日志"},
                    "type": "primary",
                    "url": log_url,
                }],
            },
        ],
    }
    plain_text = title + "\n" + f"原因：{reason}" + "\n[ 查看运行日志 ]"
    return card, plain_text


def extract_report_run_id(sync_report_path):
    """
    从 sync_report.md 里把它自己记录的 GITHUB_RUN_ID 读出来，用于新鲜度校验。
    文件不存在 / 没有这一行都返回 None（调用方按"校验不过"处理，不是当成"跳过"）。
    """
    if not os.path.exists(sync_report_path):
        return None
    with open(sync_report_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"GITHUB_RUN_ID[:：]\s*(\S+)", text)
    if not m:
        return None
    return m.group(1)


def deliver_card(card, plain_text, label):
    """统一的发送/演练出口，四条判定路径共用，避免重复代码。"""
    if DRY_RUN:
        log("FEISHU_DRY_RUN=1，仅打印卡片内容，不发送网络请求。")
        print("=== card_type:", label, "===")
        print("=== card JSON ===")
        print(json.dumps({"msg_type": "interactive", "card": card}, ensure_ascii=False, indent=2))
        print("=== 渲染后的文字内容 ===")
        print(plain_text)
        return

    try:
        status, _body = send_to_feishu(FEISHU_WEBHOOK, FEISHU_SECRET, card)
        log(f"飞书推送完成，HTTP 状态码 {status}。")
    except urllib.error.HTTPError as e:
        log(f"飞书推送失败：HTTP {e.code}（不影响主流程）。")
    except urllib.error.URLError as e:
        reason_name = type(e.reason).__name__ if e.reason else ""
        log(f"飞书推送失败：网络错误 {reason_name}（不影响主流程）。")
    except Exception as e:
        log(f"飞书推送失败：{type(e).__name__}（不影响主流程）。")


def build_success_card(stats, dashboard_url):
    y, m = stats["year"], stats["month"]
    title = f"✅ {y}年{m}月销量数据已更新"

    line1_parts = [f"乘用车总销量 **{fmt_wan(stats['total'])}辆**"]
    change_bits = []
    if stats["mom_pct"] is not None:
        change_bits.append(f"环比 {fmt_pct_signed(stats['mom_pct'])}")
    if stats["yoy_pct"] is not None:
        change_bits.append(f"同比 {fmt_pct_signed(stats['yoy_pct'])}")
    if change_bits:
        line1_parts.append(f"（{' / '.join(change_bits)}）")
    line1 = "".join(line1_parts)

    lines = [line1]
    if stats["nev_rate"] is not None:
        nev_line = f"新能源渗透率 **{stats['nev_rate']:.1f}%**"
        if stats["nev_rate_pp_change"] is not None:
            nev_line += f"（较上月 {fmt_pp_signed(stats['nev_rate_pp_change'])}）"
        lines.append(nev_line)

    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}]

    if stats["top5"]:
        top5_text = " / ".join(f"{brand} {fmt_wan(sales)}" for brand, sales in stats["top5"])
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**品牌 Top 5**：{top5_text}"}})

    new_models = stats["new_unclassified"]
    if new_models:
        shown = new_models[:3]
        names = "、".join(shown)
        if len(new_models) > 3:
            names += " 等"
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"⚠️ {len(new_models)} 个新车型待确认归类：{names}"},
        })

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "打开看板"},
            "type": "primary",
            "url": dashboard_url or "https://github.com",
        }],
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "green"},
        "elements": elements,
    }

    plain_text = title + "\n" + "\n".join(lines).replace("**", "")
    if stats["top5"]:
        plain_text += "\n─────────────────\n品牌 Top 5：" + " / ".join(
            f"{brand} {fmt_wan(sales)}" for brand, sales in stats["top5"]
        )
    if new_models:
        shown = new_models[:3]
        names = "、".join(shown)
        if len(new_models) > 3:
            names += " 等"
        plain_text += f"\n─────────────────\n⚠️ {len(new_models)} 个新车型待确认归类：{names}"
    plain_text += "\n[ 打开看板 ]"

    return card, plain_text


def build_alert_card(target_ym, latest_csv_ym, reason, log_url):
    ty, tm = target_ym
    title = f"❌ {ty}年{tm}月销量数据同步异常"

    content_lines = [f"**原因**：{reason}"]
    if latest_csv_ym:
        ly, lm = latest_csv_ym
        content_lines.append(f"数据仍停留在 **{ly}年{lm}月**")
    else:
        content_lines.append("数据仍停留在 **（无可用历史数据）**")

    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "red"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(content_lines)}},
            {
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看运行日志"},
                    "type": "primary",
                    "url": log_url,
                }],
            },
        ],
    }

    plain_text = title + "\n" + "\n".join(content_lines).replace("**", "") + "\n[ 查看运行日志 ]"
    return card, plain_text


# ---------------------------------------------------------------------------
# 组装：读数据 -> 判断成功/告警 -> 建卡片
# ---------------------------------------------------------------------------

def build_card_for_run(sales_csv_path, sync_report_path, dashboard_url, log_url):
    rows = load_sales(sales_csv_path)
    months = sorted(set((r["year"], r["month"]) for r in rows))
    latest_csv_ym = months[-1] if months else None
    latest_total, _, _ = month_totals(rows, *latest_csv_ym) if latest_csv_ym else (None, 0, {})

    report_text = ""
    if os.path.exists(sync_report_path):
        with open(sync_report_path, encoding="utf-8") as f:
            report_text = f.read()
    report_info = parse_sync_report(report_text)

    is_alert, reason = decide_alert(report_info, latest_csv_ym, latest_total)

    if is_alert:
        target_ym = determine_target_month(report_info, latest_csv_ym)
        card, plain_text = build_alert_card(target_ym, latest_csv_ym, reason, log_url)
        return True, card, plain_text

    stats = compute_month_stats(rows, *latest_csv_ym)
    card, plain_text = build_success_card(stats, dashboard_url)
    return False, card, plain_text


# ---------------------------------------------------------------------------
# 发送（含可选加签）
# ---------------------------------------------------------------------------

def gen_sign(secret, timestamp):
    """
    飞书自定义机器人加签算法：
      string_to_sign = f"{timestamp}\\n{secret}"
      sign = base64( HMAC-SHA256(key=string_to_sign, message=b"") )
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def send_to_feishu(webhook, secret, card):
    payload = {"msg_type": "interactive", "card": card}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = gen_sign(secret, timestamp)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, body


# ---------------------------------------------------------------------------
# main —— 任何异常都兜住，绝不让 workflow 失败
# ---------------------------------------------------------------------------

def main():
    try:
        if not FEISHU_WEBHOOK:
            log("未设置 FEISHU_WEBHOOK，跳过飞书推送（如需推送请在仓库 Secrets 中配置）。")
            sys.exit(0)

        log_url = run_url_fallback()

        # -----------------------------------------------------------------
        # 判定优先级：先判"这次同步跑没跑成"，再判内容。
        # 理由：sync_report.md / sales.csv 随时可能是上一次成功运行遗留下来的旧文件——
        # 同步步骤这次如果失败/中途崩溃，报告文件根本没被更新过。如果先去读报告内容，
        # 读到的会是"看起来一切正常"的旧数据，发出一张内容陈旧的"成功"卡片——
        # 这比不发通知更糟：用户会以为数据是最新的，实际上早就停更了。
        # -----------------------------------------------------------------

        # 优先级 1：同步步骤本身没有成功（failure / cancelled / skipped）——
        # 无条件发告警，完全不读 sync_report.md，因为它此时完全不可信。
        if SYNC_OUTCOME and SYNC_OUTCOME != "success":
            title = "❌ 数据同步执行失败"
            reason = (
                f"同步步骤本次执行结果为「{SYNC_OUTCOME}」，本次未产生新数据。"
                "本通知不读取 sync_report.md ——它此时可能是上一次成功运行遗留下来的旧文件，"
                "内容不代表本次状态。"
            )
            card, plain_text = build_generic_alert_card(title, reason, log_url)
            deliver_card(card, plain_text, "ALERT(sync_outcome!=success)")
            sys.exit(0)

        # 优先级 2：同步步骤 GitHub Actions 层面标记成功，但 sync.py 自己没跑到写
        # noteworthy 这个 output 就退出了（比如脚本中途抛出了顶层没兜住的异常，
        # 或者进程被信号杀死）——同样不可信任报告内容。
        if SYNC_OUTCOME == "success" and SYNC_NOTEWORTHY == "":
            title = "❌ 同步脚本异常退出"
            reason = (
                "同步脚本没有跑到写运行摘要（noteworthy）那一步就异常退出了，"
                "未能产出本次运行摘要。sync_report.md 的内容可能是上一次成功运行遗留下来的，"
                "不代表本次状态。"
            )
            card, plain_text = build_generic_alert_card(title, reason, log_url)
            deliver_card(card, plain_text, "ALERT(noteworthy=empty)")
            sys.exit(0)

        # 优先级 3：明确的幂等空跑——什么都没发生，不产生通知噪音。
        if SYNC_NOTEWORTHY == "false":
            log("本次同步是幂等空跑（没有新数据，也没有失败/放弃/拦截），跳过飞书推送。")
            sys.exit(0)

        # 优先级 4：走到这里说明同步这次确实"正常运行完了并且有值得关注的事情"
        # （或者 SYNC_OUTCOME/SYNC_NOTEWORTHY 压根没传，兼容脚本被单独手动调用的场景）。
        # 按原有逻辑读 sync_report.md / sales.csv，构建成功或告警卡片。
        try:
            is_alert, card, plain_text = build_card_for_run(
                SALES_CSV_PATH, SYNC_REPORT_PATH, DASHBOARD_URL, log_url
            )
        except Exception as e:
            log(f"构建飞书卡片失败（{type(e).__name__}），跳过本次推送。")
            sys.exit(0)

        # 新鲜度自检（双保险）：就算上面三条判定都放行了，也再确认一遍
        # sync_report.md 里记录的 GITHUB_RUN_ID 确实是本次运行写的，不是残留的旧文件。
        # 本地测试时 GITHUB_RUN_ID 这个变量通常不存在——这时候没法比对，直接跳过检查，
        # 不能因为拿不到这个变量就误报。
        if GITHUB_RUN_ID:
            report_run_id = extract_report_run_id(SYNC_REPORT_PATH)
            if report_run_id != GITHUB_RUN_ID:
                log(
                    f"!! 新鲜度校验未通过：sync_report.md 记录的 GITHUB_RUN_ID="
                    f"{report_run_id!r}，本次运行的 GITHUB_RUN_ID={GITHUB_RUN_ID!r}，不一致。"
                )
                title = "❌ 通知新鲜度校验失败"
                reason = (
                    f"sync_report.md 里记录的运行编号（{report_run_id or '（未记录）'}）"
                    f"与本次运行编号（{GITHUB_RUN_ID}）不一致，报告可能不是本次运行生成的，"
                    "数据可能未更新。"
                )
                card, plain_text = build_generic_alert_card(title, reason, log_url)
                is_alert = True
            else:
                log("新鲜度校验通过：sync_report.md 确实是本次运行生成的。")
        else:
            log("本地未提供 GITHUB_RUN_ID，跳过新鲜度校验。")

        deliver_card(card, plain_text, "ALERT" if is_alert else "SUCCESS")
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        # 最外层兜底：任何没被上面捕获到的异常，也绝不能让 workflow 失败
        log(f"notify_feishu 出现未预期的异常（{type(e).__name__}），已忽略，不影响主流程。")
        sys.exit(0)


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# 参考来源（卡片 JSON 结构 与 加签算法均查证于此，未凭记忆编写；
# 由于 open.feishu.cn 官方文档页面是前端渲染的 SPA，直接抓取只能拿到 meta 信息，
# 因此改为交叉核对多个独立信源，三方结构/算法完全一致）：
#   - 飞书官方内容页（feishu.cn 官方域名）《手把手教你通过飞书 Webhook 打造一个消息推送 Bot》
#     https://www.feishu.cn/content/7271149634339422210
#   - CSDN《飞书自定义机器人消息接入指南》（含 interactive card 完整 JSON + Java 加签代码）
#     https://blog.csdn.net/qq_43108153/article/details/136166075
#   - 阿里云开发者社区《自定义飞书Webhook机器人api接口》
#     https://developer.aliyun.com/article/1651148
#   - 飞书开放平台官方文档索引页（因 SPA 无法直接抓取正文，仅作为权威出处标注）：
#     https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN
#     https://open.feishu.cn/document/feishu-cards/quick-start/send-message-cards-with-custom-bot?lang=zh-CN
# ---------------------------------------------------------------------------

