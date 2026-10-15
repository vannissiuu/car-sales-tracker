#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：「品牌哨兵命中」告警 detect_sentinel_brand_models。
覆盖：
  1. 哨兵厂商下、不在 model_to_brand 里的车型 -> 命中，sentinel_brand 等于 manufacturer_to_brand[厂商]。
  2. 哨兵厂商下、已在 model_to_brand 里的车型 -> 不命中。
  3. 非哨兵厂商下、不在 model_to_brand 里的车型 -> 不命中（比亚迪/元UP：brand == manufacturer 但完全正确）。
  4. mapping 里没有 sentinel_manufacturers 键 -> 不报错、返回空。
  5. 跨月聚合：累计销量 / 月数 / 首末月正确，且按累计销量降序。
  6. 截断到 20，total_count 是完整个数。
  7. 纯函数，不改传入对象。
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
    "sentinel_manufacturers": ["长城汽车", "上汽集团"],
    "manufacturer_to_brand": {"长城汽车": "长城汽车", "上汽集团": "上汽集团", "比亚迪": "比亚迪"},
    "model_to_brand": {"哈弗H6": "哈弗", "MG7": "MG"},
}


def row(year, month, manufacturer, model, sales, brand=None):
    return {
        "year": year, "month": month, "manufacturer": manufacturer,
        "brand": brand or MAPPING["manufacturer_to_brand"].get(manufacturer, manufacturer),
        "model": model, "body_type": "SUV", "energy_type": "燃油", "sales": sales,
    }


def main():
    # ---- 1. 命中 ----
    res, res_n = S.detect_sentinel_brand_models([row(2026, 9, "上汽集团", "MG 07", 14577)], MAPPING)
    check("哨兵厂商 + 不在 model_to_brand -> 命中 1 款", len(res) == 1 and res[0]["model"] == "MG 07")
    check("sentinel_brand == manufacturer_to_brand[厂商]",
          res[0]["sentinel_brand"] == MAPPING["manufacturer_to_brand"]["上汽集团"])
    check("字段齐全且取值正确",
          res[0] == {"manufacturer": "上汽集团", "model": "MG 07", "sentinel_brand": "上汽集团",
                     "sales": 14577, "months": 1, "first": "2026-09", "last": "2026-09"})

    # sentinel_brand 取自 manufacturer_to_brand，而不是写死厂商名
    m2 = copy.deepcopy(MAPPING)
    m2["manufacturer_to_brand"]["长城汽车"] = "某兜底品牌"
    res, res_n = S.detect_sentinel_brand_models([row(2026, 9, "长城汽车", "长城大狗", 9265)], m2)
    check("sentinel_brand 跟随 manufacturer_to_brand 的取值", res[0]["sentinel_brand"] == "某兜底品牌")

    # ---- 2. 已在 model_to_brand ----
    res, res_n = S.detect_sentinel_brand_models(
        [row(2026, 9, "长城汽车", "哈弗H6", 30000, brand="哈弗"), row(2026, 9, "上汽集团", "MG7", 500, brand="MG")],
        MAPPING)
    check("哨兵厂商下已在 model_to_brand 的车型不命中", res == [])

    # ---- 3. 非哨兵厂商 ----
    res, res_n = S.detect_sentinel_brand_models([row(2026, 9, "比亚迪", "元UP", 20000)], MAPPING)
    check("非哨兵厂商（比亚迪/元UP，brand == manufacturer）不命中", res == [])

    # ---- 4. 无 sentinel_manufacturers 键 ----
    no_key = {k: v for k, v in MAPPING.items() if k != "sentinel_manufacturers"}
    try:
        res, res_n = S.detect_sentinel_brand_models([row(2026, 9, "上汽集团", "MG 07", 100)], no_key)
        check("mapping 没有 sentinel_manufacturers 键 -> 不报错且为空", res == [] and res_n == 0)
    except Exception as e:
        check(f"mapping 没有 sentinel_manufacturers 键 -> 不报错（抛了 {e!r}）", False)
    check("空 mapping / 空 rows 不报错", S.detect_sentinel_brand_models([], {}) == ([], 0))

    # ---- 5. 跨月聚合 ----
    rows = [
        row(2026, 8, "长城汽车", "哈弗大狗X", 100),
        row(2026, 9, "长城汽车", "哈弗大狗X", 250),
        row(2026, 7, "长城汽车", "哈弗大狗X", 50),
        row(2026, 9, "上汽集团", "飞凡Z", 900),
    ]
    res, res_n = S.detect_sentinel_brand_models(rows, MAPPING)
    check("按累计销量降序（飞凡Z 900 > 哈弗大狗X 400）", [r["model"] for r in res] == ["飞凡Z", "哈弗大狗X"])
    dog = res[1]
    check("累计销量 400", dog["sales"] == 400)
    check("月数 3", dog["months"] == 3)
    check("首末月 2026-07 / 2026-08..2026-09", dog["first"] == "2026-07" and dog["last"] == "2026-09")
    cross_year, cross_year_n = S.detect_sentinel_brand_models(
        [row(2025, 12, "长城汽车", "X", 1), row(2026, 1, "长城汽车", "X", 2)], MAPPING)
    check("跨年首末月 2025-12 ~ 2026-01", cross_year[0]["first"] == "2025-12" and cross_year[0]["last"] == "2026-01")

    # ---- 6. 截断 ----
    many = [row(2026, 9, "长城汽车", f"虚构车型{i:02d}", 1000 - i) for i in range(25)]
    res, res_n = S.detect_sentinel_brand_models(many, MAPPING)
    check("截断到 20 项", len(res) == 20)
    check("total_count 是完整个数 25", res_n == 25)
    check("截断保留的是销量最大的 20 项", res[0]["model"] == "虚构车型00" and res[-1]["model"] == "虚构车型19")
    res_small, res_small_n = S.detect_sentinel_brand_models(many[:3], MAPPING)
    check("不足 20 项时 total_count == len", len(res_small) == 3 and res_small_n == 3)

    # ---- 7. 纯函数 ----
    rows_in = copy.deepcopy(rows)
    mapping_in = copy.deepcopy(MAPPING)
    S.detect_sentinel_brand_models(rows_in, mapping_in)
    check("不改传入的 rows", rows_in == rows)
    check("不改传入的 mapping", mapping_in == MAPPING)


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
