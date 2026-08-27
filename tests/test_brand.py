#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：品牌维度 (brand 列)。
覆盖：
  1. 优先级: model_to_brand 命中时压过 manufacturer_to_brand
  2. 兜底: 两个都没命中 -> brand == manufacturer，且不为空字符串
  3. 存量补列: 没有 brand 列的旧数据，能正确补上，且幂等（连跑三次结果一致）
  4. 自举: data/mapping.json 不存在时会被创建；已存在时绝不被覆盖
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
