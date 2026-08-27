#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：本轮改动的核心——
  1. 两厢车/三厢车 -> 轿车 的分类映射 + 优先级裁决 (SUV > MPV > 轿车 > 运动汽车)
  2. normalize_legacy_body_types 对存量数据的幂等原地规范化
  3. reconcile_categories 的并集去重对账口径

全部基于 sync_script.py 里实际会跑的纯函数，不发任何网络请求。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

FAILURES = []

# label_rows() 这一轮加了 mapping 参数（品牌解析用），这些测试不关心品牌，空字典即可。
EMPTY_BRAND_MAPPING = {"manufacturer_to_brand": {}, "model_to_brand": {}}


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def make_row(year, month, model, body_type, manufacturer="厂商X", sales=100):
    return {
        "year": year, "month": month, "manufacturer": manufacturer, "model": model,
        "body_type": body_type, "energy_type": "燃油", "sales": sales,
    }


def main():
    print("=== 断言 1: 朗逸 同时在 body-1(两厢车) 和 body-2(三厢车) -> 最终 body_type=轿车，对账不产生不平 ===")
    body_lists_1 = [
        (1, "两厢车", [{"rank": 1, "model": "朗逸", "manufacturer": "上汽大众", "sales": 32419}]),
        (2, "三厢车", [{"rank": 1, "model": "朗逸", "manufacturer": "上汽大众", "sales": 32419}]),
    ]
    body_type_map_1, conflicts_1, cross_1 = S.build_body_type_map(body_lists_1)
    check("朗逸 最终类别是「轿车」", body_type_map_1.get("朗逸") == "轿车")
    check("不产生「关联冲突」告警 (两厢/三厢同款车是预期行为，不是异常)", conflicts_1 == [])
    check("朗逸 出现在跨分类车型列表里 (occurrences 长度为2)",
          len(cross_1) == 1 and cross_1[0]["model"] == "朗逸" and len(cross_1[0]["occurrences"]) == 2)

    style_rows_1 = [{"rank": 1, "model": "朗逸", "manufacturer": "上汽大众", "sales": 32419}]
    out_rows_1, other_1 = S.label_rows(style_rows_1, body_type_map_1, set(), EMPTY_BRAND_MAPPING, 2024, 1)
    recon_1 = S.reconcile_categories(out_rows_1, body_lists_1)
    sedan_result = next(r for r in recon_1["results"] if r["final_category"] == "轿车")
    check("轿车对账: page_side(并集, 朗逸只算1次)=1, actual=1, 不产生不平",
          sedan_result["page_side"] == 1 and sedan_result["actual"] == 1 and sedan_result["mismatch"] is False)
    check("总量对账通过", recon_1["total_check"]["ok"] is True)

    print()
    print("=== 断言 2: 楼兰 同时在 body-2(三厢车) 和 body-5(SUV) -> 优先级裁决后 body_type=SUV ===")
    body_lists_2 = [
        (2, "三厢车", [{"rank": 1, "model": "楼兰", "manufacturer": "东风日产", "sales": 3000}]),
        (5, "SUV", [{"rank": 1, "model": "楼兰", "manufacturer": "东风日产", "sales": 3000}]),
    ]
    body_type_map_2, conflicts_2, cross_2 = S.build_body_type_map(body_lists_2)
    check("楼兰 最终类别是「SUV」(SUV 优先级高于轿车)", body_type_map_2.get("楼兰") == "SUV")
    check("不产生「关联冲突」告警", conflicts_2 == [])
    check("楼兰 出现在跨分类车型列表里，且 final_category=SUV",
          len(cross_2) == 1 and cross_2[0]["final_category"] == "SUV")

    print()
    print("=== 断言 3: normalize_legacy_body_types 幂等性 —— 同一批数据连跑三次结果完全一致 ===")
    legacy_rows = [
        make_row(2024, 1, "长安CS75PLUS", "SUV"),
        make_row(2024, 1, "MG4", "两厢车"),
        make_row(2024, 1, "帕萨特", "三厢车"),
        make_row(2024, 1, "五菱宏光", "其他"),
        make_row(2024, 1, "宋PLUS新能源", "SUV"),
    ]
    run1, changed1 = S.normalize_legacy_body_types(legacy_rows)
    run2, changed2 = S.normalize_legacy_body_types(run1)
    run3, changed3 = S.normalize_legacy_body_types(run2)
    check(f"第1次跑改了2行 (两厢车+三厢车)，changed1={changed1}", changed1 == 2)
    check(f"第2次跑不再改任何行 (已经是轿车了)，changed2={changed2}", changed2 == 0)
    check(f"第3次跑同样不改，changed3={changed3}", changed3 == 0)
    check("三次跑完结果 (run1==run2==run3) 完全一致", run1 == run2 == run3)
    check("原始输入 legacy_rows 没有被原地修改 (纯函数，不产生副作用)",
          legacy_rows[1]["body_type"] == "两厢车" and legacy_rows[2]["body_type"] == "三厢车")

    print()
    print("=== 断言 4: 存量数据里的 两厢车/三厢车 被正确改写成 轿车，SUV/MPV/其他 不受影响 ===")
    mixed_rows = [
        make_row(2024, 1, "长安CS75PLUS", "SUV"),
        make_row(2024, 1, "MG4", "两厢车"),
        make_row(2024, 1, "帕萨特", "三厢车"),
        make_row(2024, 1, "五菱宏光", "其他"),
        make_row(2024, 1, "别克GL8", "MPV"),
        make_row(2024, 1, "Model3", "运动汽车"),
    ]
    normalized, changed = S.normalize_legacy_body_types(mixed_rows)
    by_model = {r["model"]: r["body_type"] for r in normalized}
    check("MG4 (原两厢车) -> 轿车", by_model["MG4"] == "轿车")
    check("帕萨特 (原三厢车) -> 轿车", by_model["帕萨特"] == "轿车")
    check("长安CS75PLUS (SUV) 不受影响", by_model["长安CS75PLUS"] == "SUV")
    check("五菱宏光 (其他) 不受影响", by_model["五菱宏光"] == "其他")
    check("别克GL8 (MPV) 不受影响", by_model["别克GL8"] == "MPV")
    check("Model3 (运动汽车) 不受影响", by_model["Model3"] == "运动汽车")
    check("changed 计数正确 (只有2行被改)", changed == 2)

    print()
    print("=== 断言 5: 轿车对账用并集去重 —— 两厢车页面5条、三厢车页面5条、重叠2条 -> 期望值是8不是10 ===")
    hatchback_models = [f"车型{c}" for c in "ABCDE"]           # 两厢车页面: A B C D E (5个)
    sedan_models = [f"车型{c}" for c in "DEFGH"]                # 三厢车页面: D E F G H (5个，与两厢重叠 D E)
    body_lists_5 = [
        (1, "两厢车", [{"rank": i + 1, "model": m, "manufacturer": "厂商X", "sales": 100} for i, m in enumerate(hatchback_models)]),
        (2, "三厢车", [{"rank": i + 1, "model": m, "manufacturer": "厂商X", "sales": 100} for i, m in enumerate(sedan_models)]),
    ]
    union_models = set(hatchback_models) | set(sedan_models)
    check(f"预置数据本身的并集大小确实是8 (两组各5个，重叠2个): {sorted(union_models)}", len(union_models) == 8)

    body_type_map_5, conflicts_5, cross_5 = S.build_body_type_map(body_lists_5)
    check("重叠的2个车型 (D,E) 都被记录进跨分类车型列表", len(cross_5) == 2)

    # 本月主榜恰好覆盖这 8 个不同车型，各自应该都被标成"轿车"
    style_rows_5 = [{"rank": i + 1, "model": m, "manufacturer": "厂商X", "sales": 100}
                     for i, m in enumerate(sorted(union_models))]
    out_rows_5, other_5 = S.label_rows(style_rows_5, body_type_map_5, set(), EMPTY_BRAND_MAPPING, 2024, 1)
    recon_5 = S.reconcile_categories(out_rows_5, body_lists_5)
    sedan_result_5 = next(r for r in recon_5["results"] if r["final_category"] == "轿车")

    check(f"轿车对账 page_side (并集去重) == 8，不是 10 (实际: {sedan_result_5['page_side']})",
          sedan_result_5["page_side"] == 8)
    check(f"轿车对账 actual == 8 (本月主榜8个车型全都在) (实际: {sedan_result_5['actual']})",
          sedan_result_5["actual"] == 8)
    check("并集去重后 page_side == actual，不产生对账不平", sedan_result_5["mismatch"] is False)

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
