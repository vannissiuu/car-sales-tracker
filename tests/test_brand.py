#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：品牌维度 (brand 列)。
覆盖：
  1. 优先级: model_to_brand 命中时压过 manufacturer_to_brand
  2. 兜底: 两个都没命中 -> brand == manufacturer，且不为空字符串
  3. 存量补列: 没有 brand 列的旧数据，能正确补上，且幂等（连跑三次结果一致）
  4. 自举: data/mapping.json 不存在时会被创建；已存在时绝不被覆盖
  5. model_to_manufacturer: 车型挑窝时厂商字段的强制覆盖，以及"先规范化厂商、
     再解析品牌"的应用顺序；存量厂商规范化 normalize_legacy_manufacturer_column
     的原地改写 + 联动重新解析 brand + 幂等
  8. normalize_brand_column: 把 brand 当成每次运行都从 mapping.json 重新推导的派生列，
     命中改写、不命中不动、幂等、不污染传入的 dict
全部基于 sync_script.py 里实际会跑的函数，不发任何网络请求。
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

FAILURES = []
SCRATCH_ROOT = "/tmp/p1-sync/test_brand_scratch"


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def make_row(year, month, model, manufacturer, brand=None, sales=100, body_type="SUV"):
    row = {
        "year": year, "month": month, "manufacturer": manufacturer, "model": model,
        "body_type": body_type, "energy_type": "燃油", "sales": sales,
    }
    if brand is not None:
        row["brand"] = brand
    return row


def main():
    print("=== 断言 1: model_to_brand 命中时压过 manufacturer_to_brand ===")
    # 长城汽车整车厂对应多个品牌，manufacturer_to_brand 只能给一个默认值（比如"长城"），
    # 但哈弗H6这个车型应该按 model_to_brand 精确解析成"哈弗"，不能被厂商级映射盖过去。
    mapping_1 = {
        "manufacturer_to_brand": {"长城汽车": "长城"},
        "model_to_brand": {"哈弗H6": "哈弗", "坦克300": "坦克"},
    }
    brand, source = S.resolve_brand_with_source("哈弗H6", "长城汽车", mapping_1)
    check(f"哈弗H6(长城汽车) -> brand='哈弗'(来自model_to_brand)，实际: brand={brand!r}, source={source!r}",
          brand == "哈弗" and source == "model")

    brand2, source2 = S.resolve_brand_with_source("坦克300", "长城汽车", mapping_1)
    check(f"坦克300(长城汽车) -> brand='坦克'(来自model_to_brand)，实际: brand={brand2!r}, source={source2!r}",
          brand2 == "坦克" and source2 == "model")

    # 同一个厂商下，没有被 model_to_brand 专门覆盖的车型，应该落到 manufacturer_to_brand
    brand3, source3 = S.resolve_brand_with_source("长城炮", "长城汽车", mapping_1)
    check(f"长城炮(长城汽车，不在model_to_brand里) -> brand='长城'(来自manufacturer_to_brand)，"
          f"实际: brand={brand3!r}, source={source3!r}",
          brand3 == "长城" and source3 == "manufacturer")

    print()
    print("=== 断言 2: 两个映射都没命中 -> brand == manufacturer，且不为空字符串 ===")
    mapping_2 = {"manufacturer_to_brand": {"上汽大众": "大众"}, "model_to_brand": {"哈弗H6": "哈弗"}}
    brand4, source4 = S.resolve_brand_with_source("某冷门车型", "某冷门厂商", mapping_2)
    check(f"某冷门厂商 不在字典里 -> brand==manufacturer=='某冷门厂商'，实际: brand={brand4!r}, source={source4!r}",
          brand4 == "某冷门厂商" and source4 == "fallback")
    check("回退的 brand 不是空字符串", brand4 != "")

    # resolve_brand() 简化版接口同样验证一遍
    brand5 = S.resolve_brand("某冷门车型2", "另一个冷门厂商", mapping_2)
    check(f"resolve_brand() 简化接口同样正确回退: brand={brand5!r}", brand5 == "另一个冷门厂商" and brand5 != "")

    print()
    print("=== 断言 3: 存量补列 —— 没有 brand 列的旧数据，正确补上，且幂等(连跑三次一致) ===")
    mapping_3 = {"manufacturer_to_brand": {"上汽大众": "大众", "长安汽车": "长安"}, "model_to_brand": {}}
    legacy_rows_no_brand = [
        make_row(2024, 1, "朗逸", "上汽大众"),               # 没有 brand 键 (旧数据)
        make_row(2024, 1, "长安CS75PLUS", "长安汽车"),        # 没有 brand 键 (旧数据)
        make_row(2024, 1, "某冷门车", "某冷门厂商"),           # 没有 brand 键，且厂商不在字典里
    ]
    check("预置数据确实没有 brand 键(模拟旧版本 sales.csv)",
          all("brand" not in r for r in legacy_rows_no_brand))

    run1, changed1 = S.normalize_legacy_brand_column(legacy_rows_no_brand, mapping_3)
    run2, changed2 = S.normalize_legacy_brand_column(run1, mapping_3)
    run3, changed3 = S.normalize_legacy_brand_column(run2, mapping_3)

    check(f"第1次跑补齐了全部3行的 brand 列，changed1={changed1}", changed1 == 3)
    check(f"第2次跑不再改任何行 (已经都有 brand 了)，changed2={changed2}", changed2 == 0)
    check(f"第3次跑同样不改，changed3={changed3}", changed3 == 0)
    check("三次跑完结果 (run1==run2==run3) 完全一致", run1 == run2 == run3)

    by_model = {r["model"]: r["brand"] for r in run1}
    check("朗逸 -> brand=大众 (manufacturer_to_brand 命中)", by_model["朗逸"] == "大众")
    check("长安CS75PLUS -> brand=长安", by_model["长安CS75PLUS"] == "长安")
    check("某冷门车 -> brand=某冷门厂商 (回退，不为空)",
          by_model["某冷门车"] == "某冷门厂商" and by_model["某冷门车"] != "")
    check("原始输入 legacy_rows_no_brand 没有被原地修改 (纯函数，不产生副作用)",
          all("brand" not in r for r in legacy_rows_no_brand))

    print()
    print("=== 断言 5: model_to_manufacturer —— 车型挑窝时厂商字段的强制覆盖 ===")
    # 欧拉好猫在数据源里长期挂在"长城新能源"下，某个月数据源把它错挪到了"长城汽车"
    # （两个厂商名本身一直并存，不是改名）。resolve_manufacturer 应该按车型名精确匹配，
    # 把它强制改回"长城新能源"；不在表里的车型原样不动。
    mapping_5 = {
        "model_to_manufacturer": {"欧拉好猫": "长城新能源"},
        "manufacturer_to_brand": {"长城汽车": "长城汽车", "长城新能源": "欧拉"},
        "model_to_brand": {"欧拉好猫": "欧拉"},
    }
    mfr_hit = S.resolve_manufacturer("欧拉好猫", "长城汽车", mapping_5)
    check(f"欧拉好猫(数据源给的是长城汽车) -> 强制改回'长城新能源'，实际: {mfr_hit!r}",
          mfr_hit == "长城新能源")

    mfr_already_right = S.resolve_manufacturer("欧拉好猫", "长城新能源", mapping_5)
    check(f"欧拉好猫(数据源本来就给长城新能源) -> 原样是'长城新能源'，实际: {mfr_already_right!r}",
          mfr_already_right == "长城新能源")

    mfr_miss = S.resolve_manufacturer("哈弗H6", "长城汽车", mapping_5)
    check(f"哈弗H6(不在 model_to_manufacturer 里) -> 原样返回厂商'长城汽车'，实际: {mfr_miss!r}",
          mfr_miss == "长城汽车")

    mfr_no_table = S.resolve_manufacturer("欧拉好猫", "长城汽车", {})
    check(f"mapping 里压根没有 model_to_manufacturer 这个键 -> 不抛异常，原样返回'长城汽车'，"
          f"实际: {mfr_no_table!r}", mfr_no_table == "长城汽车")

    print()
    print("=== 断言 6: model_to_manufacturer 必须先于 resolve_brand_with_source 应用 ===")
    # 单独验证"应用顺序"本身要紧：故意不给 model_to_brand 留双保险（模拟只做了改动1、
    # 没做改动1里"顺手补一条 model_to_brand"那一步的情况），只靠 manufacturer_to_brand
    # 兜底。此时如果厂商没有先规范化就去解析品牌，会拿着错误的厂商名'长城汽车'去查
    # manufacturer_to_brand，解析出一个错误的兜底品牌'长城汽车'，而不是正确的'欧拉'。
    mapping_6_no_double_insurance = {
        "model_to_manufacturer": {"欧拉好猫": "长城新能源"},
        "manufacturer_to_brand": {"长城汽车": "长城汽车", "长城新能源": "欧拉"},
        "model_to_brand": {},
    }
    raw_manufacturer = "长城汽车"
    fixed_manufacturer = S.resolve_manufacturer("欧拉好猫", raw_manufacturer, mapping_6_no_double_insurance)
    brand6, source6 = S.resolve_brand_with_source("欧拉好猫", fixed_manufacturer, mapping_6_no_double_insurance)
    check(f"先规范化厂商('{fixed_manufacturer}')再解析品牌 -> brand='欧拉'，实际: brand={brand6!r}, source={source6!r}",
          brand6 == "欧拉")

    wrong_brand, wrong_source = S.resolve_brand_with_source("欧拉好猫", raw_manufacturer, mapping_6_no_double_insurance)
    check(f"对照组：不做厂商规范化、直接拿原始厂商'{raw_manufacturer}'解析品牌 -> 会解析成错误的兜底值，"
          f"实际: brand={wrong_brand!r}, source={wrong_source!r}（说明应用顺序确实要紧，"
          f"哪怕 model_to_brand 双保险还没补上，只要顺序对了品牌也不会错）",
          wrong_brand != "欧拉" and wrong_source == "manufacturer")

    # 有 model_to_brand 双保险时（本项目实际采用的方案），即便万一有代码路径忘了先调用
    # resolve_manufacturer，品牌也不会错——这正是"双保险"的意义，两条断言对照着看。
    brand6b, source6b = S.resolve_brand_with_source("欧拉好猫", raw_manufacturer, mapping_5)
    check(f"对照：有 model_to_brand 双保险时，哪怕传入未规范化的原始厂商'{raw_manufacturer}'，"
          f"brand 依然正确解析成'欧拉'，实际: brand={brand6b!r}, source={source6b!r}",
          brand6b == "欧拉" and source6b == "model")

    print()
    print("=== 断言 7: normalize_legacy_manufacturer_column —— 存量厂商规范化 + 联动重新解析 brand + 幂等 ===")
    legacy_rows_mixed = [
        make_row(2024, 1, "欧拉好猫", "长城新能源", brand="欧拉", body_type="轿车"),   # 已经是对的，不该被动
        make_row(2026, 8, "欧拉好猫", "长城汽车", brand="长城汽车", body_type="轿车"),  # 挑窝了的那一行，manufacturer 和 brand 都错
        make_row(2024, 1, "哈弗H6", "长城汽车", brand="哈弗", body_type="SUV"),        # 不在 model_to_manufacturer 里，不该被动
    ]
    run7a, changed7a = S.normalize_legacy_manufacturer_column(legacy_rows_mixed, mapping_5)
    check(f"只有挑窝的那一行被改写，changed7a=1，实际: {changed7a}", changed7a == 1)

    by_ym = {(r["year"], r["month"], r["model"]): r for r in run7a}
    fixed_row = by_ym[(2026, 8, "欧拉好猫")]
    check(f"2026-08 欧拉好猫: manufacturer 改回'长城新能源'，实际: {fixed_row['manufacturer']!r}",
          fixed_row["manufacturer"] == "长城新能源")
    check(f"2026-08 欧拉好猫: brand 联动重新解析成'欧拉'（不再是错的'长城汽车'），实际: {fixed_row['brand']!r}",
          fixed_row["brand"] == "欧拉")

    unchanged_row = by_ym[(2024, 1, "欧拉好猫")]
    check("2024-01 欧拉好猫(本来就对) 原样不动",
          unchanged_row["manufacturer"] == "长城新能源" and unchanged_row["brand"] == "欧拉")

    other_model_row = by_ym[(2024, 1, "哈弗H6")]
    check("哈弗H6(不在 model_to_manufacturer 里) 原样不动",
          other_model_row["manufacturer"] == "长城汽车" and other_model_row["brand"] == "哈弗")

    check("原始输入 legacy_rows_mixed 没有被原地修改 (纯函数，不产生副作用)",
          legacy_rows_mixed[1]["manufacturer"] == "长城汽车" and legacy_rows_mixed[1]["brand"] == "长城汽车")

    run7b, changed7b = S.normalize_legacy_manufacturer_column(run7a, mapping_5)
    check(f"第2次跑不再改任何行 (已经规范化过了)，changed7b=0，实际: {changed7b}", changed7b == 0)
    check("两次跑完结果完全一致 (幂等)", run7a == run7b)

    print()
    print("=== 断言 8: normalize_brand_column —— 每次运行都从 mapping.json 重新推导 brand ===")
    # mapping.json 是品牌归属的唯一事实来源：用户在 GitHub 网页上编辑它调整品牌归属后，
    # 已经入库、已经有 brand 的存量行也必须跟着变——这正是 normalize_legacy_brand_column
    # (只补空值) 做不到、必须靠 normalize_brand_column (全量重解析) 来补的缺口。
    mapping_8 = {
        "manufacturer_to_brand": {"长城汽车": "长城汽车", "上汽大众": "大众"},
        "model_to_brand": {"长城H10": "长城", "欧拉5 EV": "欧拉", "魏牌 V8X": "魏牌"},
    }
    legacy_rows_mixed_brand = [
        # 命中：mapping.json 刚补了这条 model_to_brand，存量行的 brand 还是旧的哨兵值，要被改写
        make_row(2026, 8, "长城H10", "长城汽车", brand="长城汽车", body_type="SUV"),
        make_row(2026, 8, "欧拉5 EV", "长城汽车", brand="长城汽车", body_type="轿车"),
        make_row(2026, 8, "魏牌 V8X", "长城汽车", brand="长城汽车", body_type="SUV"),
        # 不命中：解析结果和已存的 brand 一致，不该被动
        make_row(2024, 1, "朗逸", "上汽大众", brand="大众", body_type="轿车"),
        # 不命中：不在 model_to_brand 里，manufacturer_to_brand 兜底值本来就等于已存的 brand
        make_row(2024, 1, "长城炮", "长城汽车", brand="长城汽车", body_type="SUV"),
    ]
    run8a, changed8a = S.normalize_brand_column(legacy_rows_mixed_brand, mapping_8)
    check(f"命中的 3 行(长城H10/欧拉5 EV/魏牌 V8X)被改写，其余 2 行不动，changed8a=3，实际: {changed8a}",
          changed8a == 3)

    by_model_8 = {r["model"]: r["brand"] for r in run8a}
    check("长城H10 -> brand 改写为'长城'", by_model_8["长城H10"] == "长城")
    check("欧拉5 EV -> brand 改写为'欧拉'", by_model_8["欧拉5 EV"] == "欧拉")
    check("魏牌 V8X -> brand 改写为'魏牌'", by_model_8["魏牌 V8X"] == "魏牌")
    check("朗逸(未命中，解析结果本来就和已存brand一致) -> brand 原样是'大众'", by_model_8["朗逸"] == "大众")
    check("长城炮(未命中，兜底值本来就和已存brand一致) -> brand 原样是'长城汽车'",
          by_model_8["长城炮"] == "长城汽车")

    check("原始输入 legacy_rows_mixed_brand 没有被原地修改 (纯函数，不产生副作用)",
          legacy_rows_mixed_brand[0]["brand"] == "长城汽车"
          and legacy_rows_mixed_brand[1]["brand"] == "长城汽车"
          and legacy_rows_mixed_brand[2]["brand"] == "长城汽车")

    run8b, changed8b = S.normalize_brand_column(run8a, mapping_8)
    check(f"第2次跑不再改任何行 (brand 已经是按当前 mapping 解析出来的结果)，changed8b=0，实际: {changed8b}",
          changed8b == 0)
    check("两次跑完结果完全一致 (幂等)", run8a == run8b)

    print()
    print("=== 断言 4: 自举 —— mapping.json 不存在时会被创建；已存在时绝不被覆盖 ===")

    # --- 4a: 文件不存在 -> 应该被创建，内容等于 DEFAULT_MAPPING_JSON 解析后的结果 ---
    case_a_dir = os.path.join(SCRATCH_ROOT, "case_a_no_file")
    if os.path.exists(case_a_dir):
        shutil.rmtree(case_a_dir)
    os.makedirs(case_a_dir, exist_ok=True)

    orig_data_dir, orig_mapping_path = S.DATA_DIR, S.MAPPING_JSON_PATH
    try:
        S.DATA_DIR = case_a_dir
        S.MAPPING_JSON_PATH = os.path.join(case_a_dir, "mapping.json")

        check("测试前置条件: mapping.json 确实不存在", not os.path.exists(S.MAPPING_JSON_PATH))
        result_mapping = S.load_or_bootstrap_mapping()

        check("调用后 mapping.json 文件被创建了", os.path.exists(S.MAPPING_JSON_PATH))
        with open(S.MAPPING_JSON_PATH, "r", encoding="utf-8") as f:
            written_content = f.read()
        expected_default = json.loads(S.DEFAULT_MAPPING_JSON)
        check("写出的文件内容能被 json.loads 正常解析",
              _try_json_loads(written_content) is not None)
        check("写出的内容和内置的 DEFAULT_MAPPING_JSON 解析后一致",
              _try_json_loads(written_content) == expected_default)
        check("load_or_bootstrap_mapping() 返回值也等于默认字典",
              result_mapping == expected_default)
        check("返回值带有 manufacturer_to_brand / model_to_brand 两个键",
              "manufacturer_to_brand" in result_mapping and "model_to_brand" in result_mapping)

        # --- 4b: 文件已存在(模拟用户手工改过) -> 绝不能被覆盖 ---
        case_b_dir = os.path.join(SCRATCH_ROOT, "case_b_existing_file")
        if os.path.exists(case_b_dir):
            shutil.rmtree(case_b_dir)
        os.makedirs(case_b_dir, exist_ok=True)
        S.DATA_DIR = case_b_dir
        S.MAPPING_JSON_PATH = os.path.join(case_b_dir, "mapping.json")

        user_edited_content = json.dumps({
            "_meta": {"resolution_order": "用户手工改过的说明文字，不应该被覆盖掉"},
            "manufacturer_to_brand": {"上汽大众": "大众", "东风日产": "日产", "自定义厂商": "自定义品牌"},
            "model_to_brand": {"哈弗H6": "哈弗"},
            "_unresolved_notes": ["用户加的备注"],
        }, ensure_ascii=False, indent=2)
        with open(S.MAPPING_JSON_PATH, "w", encoding="utf-8") as f:
            f.write(user_edited_content)

        with open(S.MAPPING_JSON_PATH, "r", encoding="utf-8") as f:
            before_call_bytes = f.read()

        result_mapping_b = S.load_or_bootstrap_mapping()

        with open(S.MAPPING_JSON_PATH, "r", encoding="utf-8") as f:
            after_call_bytes = f.read()

        check("已存在的 mapping.json 文件内容调用前后逐字节完全一致 (没有被覆盖)",
              before_call_bytes == after_call_bytes)
        check("已存在的 mapping.json 内容仍然是用户手工写的那份 (不是默认字典)",
              json.loads(after_call_bytes) != json.loads(S.DEFAULT_MAPPING_JSON))
        check("load_or_bootstrap_mapping() 返回的是用户手工写的字典内容",
              result_mapping_b == json.loads(user_edited_content))
        check("返回值里能看到用户自定义的 '自定义厂商' 映射",
              result_mapping_b.get("manufacturer_to_brand", {}).get("自定义厂商") == "自定义品牌")
    finally:
        S.DATA_DIR, S.MAPPING_JSON_PATH = orig_data_dir, orig_mapping_path
        if os.path.exists(SCRATCH_ROOT):
            shutil.rmtree(SCRATCH_ROOT)

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


def _try_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


if __name__ == "__main__":
    main()
