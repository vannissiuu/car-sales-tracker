#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：FORCE_REFRESH 的解析约定 + 按月粒度 upsert 的三个场景 (A/B/C)。
全部基于 sync_script.py 里实际会跑的纯函数：
  parse_force_refresh / compute_effective_months_done / merge_existing_and_new
不发任何网络请求。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

FAILURES = []


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def make_row(year, month, model, sales=100, body_type="SUV", manufacturer="厂商X"):
    return {
        "year": year, "month": month, "manufacturer": manufacturer, "model": model,
        "body_type": body_type, "energy_type": "燃油", "sales": sales,
    }


def main():
    print("=== 解析测试: parse_force_refresh 的 truthy/falsy 覆盖 ===")
    truthy_cases = ["true", "TRUE", "True", "1", "yes", "YES", "  true  ", " 1 "]
    for raw in truthy_cases:
        val, desc = S.parse_force_refresh(raw)
        check(f"FORCE_REFRESH={raw!r} -> True  (实际: {val}, {desc!r})", val is True)

    falsy_cases = ["", None, "false", "False", "FALSE", "abc", "0", "no", "  ", "2"]
    for raw in falsy_cases:
        val, desc = S.parse_force_refresh(raw)
        check(f"FORCE_REFRESH={raw!r} -> False (实际: {val}, {desc!r})", val is False)

    print()
    print("=== 场景 A: 已有 2024-01 数据，force_refresh=False -> 跳过，sales.csv 不变 ===")
    months_done = {(2024, 1)}
    force_refresh_a, _ = S.parse_force_refresh("")  # push 触发 / 未传参
    effective_a = S.compute_effective_months_done(months_done, force_refresh_a)
    check("force_refresh 解析为 False", force_refresh_a is False)
    check("effective_months_done 仍包含 (2024,1) -> 2024-01 会被跳过，不进入 targets",
          (2024, 1) in effective_a)

    existing_rows_a = [make_row(2024, 1, "长安CS75PLUS", body_type="SUV")]
    # 场景A里 2024-01 被跳过，本次运行不会产出它的新数据 -> synced_this_run 里没有它
    synced_this_run_a = set()
    merged_a = S.merge_existing_and_new(existing_rows_a, [], synced_this_run_a)
    check("sales.csv 合并结果与原来完全一致 (1行，内容不变)",
          merged_a == existing_rows_a)

    print()
    print("=== 场景 B: 已有 2024-01 数据 (旧/带bug)，force_refresh=True -> 重抓并替换，总行数不翻倍 ===")
    force_refresh_b, desc_b = S.parse_force_refresh("true")
    check("force_refresh 解析为 True", force_refresh_b is True)
    effective_b = S.compute_effective_months_done(months_done, force_refresh_b)
    check("effective_months_done 被清空 -> 2024-01 会重新进入 targets", effective_b == set())

    # 旧数据：2024-01 主榜 553 行里，两厢车关联丢了17条，这里用一个缩小版本模拟：
    # 旧版本 3 行，其中 MG4 因为厂商名不一致关联失败，body_type 是空字符串（旧 bug 的产物）
    existing_rows_b = [
        make_row(2024, 1, "长安CS75PLUS", sales=40496, body_type="SUV"),
        make_row(2024, 1, "MG4", sales=5000, body_type=""),  # 旧 bug：应该是"两厢车"，丢了
        make_row(2024, 1, "朗逸", sales=32419, body_type="其他"),
    ]
    # 本次重抓，新版本用车型名关联，MG4 正确标上了"两厢车"；行数还是 3 行（同一个月，不多不少）
    new_rows_b = [
        make_row(2024, 1, "长安CS75PLUS", sales=40496, body_type="SUV"),
        make_row(2024, 1, "MG4", sales=5000, body_type="两厢车"),  # 修复后
        make_row(2024, 1, "朗逸", sales=32419, body_type="其他"),
    ]
    synced_this_run_b = {(2024, 1)}
    merged_b = S.merge_existing_and_new(existing_rows_b, new_rows_b, synced_this_run_b)

    check("合并后总行数还是 3 行 (没有翻倍成 6 行)", len(merged_b) == 3)
    check("合并结果里没有旧版本的行 (旧 MG4 那条 body_type='' 的记录不见了)",
          not any(r["model"] == "MG4" and r["body_type"] == "" for r in merged_b))
    mg4_row = next(r for r in merged_b if r["model"] == "MG4")
    check("合并结果里 MG4 是新版本 (body_type=两厢车)", mg4_row["body_type"] == "两厢车")
    check("合并结果的 2024-01 部分完全等于 new_rows_b (逐行比较)",
          sorted(merged_b, key=lambda r: r["model"]) == sorted(new_rows_b, key=lambda r: r["model"]))

    print()
    print("=== 场景 C (最重要): 已有 2024-01 和 2024-02，本次 force_refresh=True 但 2024-02 抓取失败被放弃 ===")
    print("    -> 2024-02 的原有数据应该完整保留，不被抹掉")

    existing_rows_c = [
        make_row(2024, 1, "长安CS75PLUS", sales=40496, body_type=""),   # 2024-01 旧数据(带bug)
        make_row(2024, 1, "MG4", sales=5000, body_type=""),            # 2024-01 旧数据(带bug)
        make_row(2024, 2, "轩逸", sales=30000, body_type="其他"),        # 2024-02 旧数据(完整、没问题)
        make_row(2024, 2, "秦PLUS", sales=29000, body_type="其他"),
    ]
    # 本次运行：2024-01 重抓成功，产出修复后的新数据；2024-02 因为拦截/超时被 MonthAbortedException
    # 放弃，all_new_rows 里完全没有 2024-02 的任何行
    new_rows_c_2024_01 = [
        make_row(2024, 1, "长安CS75PLUS", sales=40496, body_type="SUV"),  # 修复后
        make_row(2024, 1, "MG4", sales=5000, body_type="两厢车"),          # 修复后
    ]
    all_new_rows_c = list(new_rows_c_2024_01)  # 2024-02 抓取失败，没有任何新行产出

    # months_done (report["months_done"]) 本次运行只会记录真正成功的月份 —— 2024-02 被放弃，
    # 不会出现在这里，所以 synced_this_run 只有 (2024,1)
    synced_this_run_c = {(2024, 1)}

    merged_c = S.merge_existing_and_new(existing_rows_c, all_new_rows_c, synced_this_run_c)

    feb_rows_in_merged = [r for r in merged_c if r["month"] == 2]
    feb_rows_original = [r for r in existing_rows_c if r["month"] == 2]

    check("2024-02 的行数没变 (2行)", len(feb_rows_in_merged) == 2)
    check("2024-02 的内容和原来逐行完全一致 (没有被抹掉/篡改)",
          sorted(feb_rows_in_merged, key=lambda r: r["model"])
          == sorted(feb_rows_original, key=lambda r: r["model"]))

    jan_rows_in_merged = [r for r in merged_c if r["month"] == 1]
    check("2024-01 已经被替换成新版本 (body_type 不再是旧 bug 的空字符串)",
          all(r["body_type"] != "" for r in jan_rows_in_merged))
    check("2024-01 行数没有翻倍 (还是2行，不是4行)", len(jan_rows_in_merged) == 2)

    check("合并后总行数 = 2024-01新(2) + 2024-02旧(2) = 4 行，不多不少",
          len(merged_c) == 4)

    print()
    print("=" * 60)
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    else:
        print("全部断言通过。")
        sys.exit(0)


if __name__ == "__main__":
    main()
