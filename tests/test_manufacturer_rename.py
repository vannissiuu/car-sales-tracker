#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：厂商整体改名自动跟随 auto_follow_manufacturer_renames。
覆盖：
  1. 车型旧月份是厂商 A、最新月份是厂商 B -> 全部行改写成 B，rows_changed / from / to / latest_month 正确。
  2. 被改写行的 brand 同步重新解析（A、B 解析出不同品牌）。
  3. 最新月份同时有 A 和 B 两行 -> 取销量大的，结果确定（与输入顺序无关）。
  4. model_to_manufacturer 里有人工裁定的车型 -> 完全不动（人工优先）。
  5. 只有单一厂商的车型 -> 逐字段原样。
  6. 幂等：第二次跑 changes 为空。
  7. 纯函数，不改传入 dict。
  8. 空输入不抛异常。
全部基于 sync_script.py 里实际会跑的纯函数，车型名/数据均为虚构或最小构造，不发任何网络请求。
"""

import copy
import os
import sys

# 指向 src/ 才能 import 到 sync_script（同 test_energy.py）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sync_script as S

FAILURES = []


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)



MAPPING = {
    "manufacturer_to_brand": {"旧厂": "旧品牌", "新厂": "新品牌", "稳定厂": "稳定品牌"},
    "model_to_brand": {},
    "model_to_manufacturer": {},
}


def row(year, month, manufacturer, model, sales, brand=None):
    return {
        "year": year, "month": month, "manufacturer": manufacturer,
        "brand": brand or MAPPING["manufacturer_to_brand"][manufacturer],
        "model": model, "body_type": "SUV", "energy_type": "新能源", "sales": sales,
    }


def main():
    # ---- 1 + 2. 改名跟随 + brand 重解析 ----
    rows = [
        row(2026, 7, "旧厂", "虚构一号", 100),
        row(2026, 8, "旧厂", "虚构一号", 200),
        row(2026, 9, "新厂", "虚构一号", 300),
    ]
    out, changes = S.auto_follow_manufacturer_renames(rows, MAPPING)
    check("全部行的 manufacturer 都是最新月份的「新厂」", [r["manufacturer"] for r in out] == ["新厂"] * 3)
    check("changes 恰好 1 项", len(changes) == 1)
    check("changes 内容正确",
          changes[0] == {"model": "虚构一号", "from": ["旧厂"], "to": "新厂",
                         "rows_changed": 2, "latest_month": "2026-09"})
    check("被改写行 brand 重新解析成「新品牌」", [r["brand"] for r in out] == ["新品牌"] * 3)
    check("销量/月份等其他字段原样",
          [(r["year"], r["month"], r["sales"], r["body_type"], r["energy_type"]) for r in out]
          == [(2026, 7, 100, "SUV", "新能源"), (2026, 8, 200, "SUV", "新能源"), (2026, 9, 300, "SUV", "新能源")])

    # model_to_brand 命中时 brand 仍按 resolve_brand 的优先级
    m_brand = dict(MAPPING, model_to_brand={"虚构一号": "固定品牌"})
    out_b, _ = S.auto_follow_manufacturer_renames(rows, m_brand)
    check("model_to_brand 优先级仍然成立（被改写的行重解析走 resolve_brand；未改写的行不碰）",
          [r["brand"] for r in out_b] == ["固定品牌", "固定品牌", "新品牌"])

    # ---- 3. 最新月份两个厂商并存 ----
    tie_rows = [
        row(2026, 8, "旧厂", "虚构二号", 50),
        row(2026, 9, "旧厂", "虚构二号", 80),
        row(2026, 9, "新厂", "虚构二号", 900),
    ]
    out1, ch1 = S.auto_follow_manufacturer_renames(tie_rows, MAPPING)
    out2, ch2 = S.auto_follow_manufacturer_renames(list(reversed(tie_rows)), MAPPING)
    check("最新月并存时取销量大的「新厂」", ch1[0]["to"] == "新厂" and {r["manufacturer"] for r in out1} == {"新厂"})
    check("输入顺序反过来结果一致（确定性）", ch2[0]["to"] == "新厂" and ch2[0]["rows_changed"] == ch1[0]["rows_changed"] == 2)
    check("from 不含目标厂商", ch1[0]["from"] == ["旧厂"])
    flip = [row(2026, 9, "旧厂", "虚构三号", 900), row(2026, 9, "新厂", "虚构三号", 80)]
    _, ch3 = S.auto_follow_manufacturer_renames(flip, MAPPING)
    check("销量大的换成「旧厂」时取「旧厂」", ch3[0]["to"] == "旧厂")
    # 销量完全相同：按厂商名字典序，且与输入顺序无关
    eq = [row(2026, 9, "旧厂", "虚构四号", 10), row(2026, 9, "新厂", "虚构四号", 10)]
    _, e1 = S.auto_follow_manufacturer_renames(eq, MAPPING)
    _, e2 = S.auto_follow_manufacturer_renames(list(reversed(eq)), MAPPING)
    check("销量相同也确定（与输入顺序无关）", e1[0]["to"] == e2[0]["to"])

    # ---- 4. 人工裁定优先 ----
    manual_map = dict(MAPPING, model_to_manufacturer={"虚构一号": "旧厂"})
    out_m, ch_m = S.auto_follow_manufacturer_renames(rows, manual_map)
    check("人工裁定的车型 changes 为空", ch_m == [])
    check("人工裁定的车型所有行完全不动", out_m == rows)

    # ---- 5. 单一厂商原样 ----
    solo = [row(2026, 8, "稳定厂", "虚构五号", 10), row(2026, 9, "稳定厂", "虚构五号", 20)]
    out_s, ch_s = S.auto_follow_manufacturer_renames(solo, MAPPING)
    check("单一厂商车型无 changes", ch_s == [])
    check("单一厂商车型逐字段原样", out_s == solo)
    mixed = rows + solo
    out_x, ch_x = S.auto_follow_manufacturer_renames(mixed, MAPPING)
    check("混合输入时只动多厂商的车型", out_x[3:] == solo and len(ch_x) == 1)
    check("输出顺序与输入顺序一致", [(r["model"], r["month"]) for r in out_x] == [(r["model"], r["month"]) for r in mixed])

    # ---- 6. 幂等 ----
    again, ch_again = S.auto_follow_manufacturer_renames(out, MAPPING)
    check("第二次跑 changes 为空", ch_again == [])
    check("第二次跑结果不变", again == out)

    # ---- 7. 纯函数 ----
    rows_in = copy.deepcopy(rows)
    mapping_in = copy.deepcopy(MAPPING)
    S.auto_follow_manufacturer_renames(rows_in, mapping_in)
    check("不改传入的 rows（含其中每个 dict）", rows_in == rows)
    check("不改传入的 mapping", mapping_in == MAPPING)

    # 多个车型：changes 按 model 排序
    multi = [
        row(2026, 8, "旧厂", "乙车", 1), row(2026, 9, "新厂", "乙车", 1),
        row(2026, 8, "旧厂", "甲车", 1), row(2026, 9, "新厂", "甲车", 1),
    ]
    _, ch_multi = S.auto_follow_manufacturer_renames(multi, MAPPING)
    check("changes 按 model 排序", [c["model"] for c in ch_multi] == sorted(c["model"] for c in ch_multi))

    # ---- 8. 空输入 ----
    try:
        out_e, ch_e = S.auto_follow_manufacturer_renames([], {})
        check("空 rows + 空 mapping 不抛异常", out_e == [] and ch_e == [])
    except Exception as e:
        check(f"空输入不抛异常（抛了 {e!r}）", False)


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
