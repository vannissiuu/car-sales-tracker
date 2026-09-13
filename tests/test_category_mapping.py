#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：本轮改动的核心——
  1. 两厢车/三厢车 -> 轿车 的分类映射 + 优先级裁决 (SUV > MPV > 轿车 > 运动汽车)
  2. normalize_legacy_body_types 对存量数据的幂等原地规范化
  3. reconcile_categories 的并集去重对账口径
  4. fetch_full_listing 的分页完整性校验与修复（销量并列车型排序在两次请求间随机，
     骑在分页边界上时会导致一个车型被重复抓到、另一个车型被挤出窗口）

全部基于 sync_script.py 里实际会跑的纯函数，不发任何网络请求
（第4部分用 monkeypatch 替换 fetch_page/parse_style_like_table 模拟"网络返回"，
不发真实请求）。
"""

import collections
import json
import os
import re
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
    print("=== 断言 6: fetch_full_listing 分页完整性校验 —— 并列漂移导致丢车型 -> 被检测到 -> 重抓补回 ===")
    print("(复现 2026-07 真实案例：菱智新能源/标致508 销量并列，两次独立请求把'标致508'都排到了")
    print(" 第10页末行/第11页首行，'菱智新能源'两页都没抓到；重抓边界两页后应该补回)")

    PAGE6_TOTAL = 520  # 11页：前10页各50条、第11页20条

    def _make_row(rank, model):
        return {"rank": rank, "model": model, "manufacturer": "厂商X", "sales": 100}

    def _canonical_model_6(rank):
        if rank == 500:
            return "菱智新能源"
        if rank == 501:
            return "标致508"
        return f"型号{rank:04d}"

    call_counts_6 = collections.defaultdict(int)

    def _rows_for_page_6(page, call_idx):
        start = (page - 1) * 50 + 1
        end = min(page * 50, PAGE6_TOTAL)
        rows = []
        for rank in range(start, end + 1):
            if rank == 500:
                # 第1次抓取复现 bug：这个名次被"标致508"占了；第2次(重抓修复)"菱智新能源"归位
                model = "标致508" if call_idx == 1 else "菱智新能源"
            elif rank == 501:
                # 两次独立请求都把"标致508"排到了这个名次上，这正是真实 bug 的样子
                model = "标致508"
            else:
                model = _canonical_model_6(rank)
            rows.append(_make_row(rank, model))
        return rows

    def _fake_fetch_page_6(session, url, allow_404_as_empty=False):
        m = re.search(r"-(\d+)\.html$", url)
        page = int(m.group(1))
        call_counts_6[page] += 1
        rows = _rows_for_page_6(page, call_counts_6[page])
        marker = f"共{PAGE6_TOTAL}条" if page == 1 else "(no total marker on this page)"
        return marker + "\n" + json.dumps(rows, ensure_ascii=False), None, 200

    def _fake_parse_style_like_table_6(html_text):
        _, _, payload = html_text.partition("\n")
        return json.loads(payload)

    _orig_fetch_page = S.fetch_page
    _orig_parse_style_like_table = S.parse_style_like_table
    try:
        S.fetch_page = _fake_fetch_page_6
        S.parse_style_like_table = _fake_parse_style_like_table_6

        report_6 = {"failures": [], "pagination_integrity": []}
        rows_6, ok_6, _page1_html_6, probe_6 = S.fetch_full_listing(
            session=object(),
            url_template="https://xl.16888.com/style-202607-202607-{page}.html",
            report=report_6,
            label="202607 主榜",
        )
    finally:
        S.fetch_page = _orig_fetch_page
        S.parse_style_like_table = _orig_parse_style_like_table

    check("fetch_full_listing 最终返回成功 (ok=True)", ok_6 is True)
    check(f"返回行数 == 声明总数 520 (实际: {len(rows_6) if rows_6 else None})",
          rows_6 is not None and len(rows_6) == PAGE6_TOTAL)
    if rows_6 is not None:
        model_counts_6 = collections.Counter(r["model"] for r in rows_6)
        check("菱智新能源被补回 (出现1次)", model_counts_6.get("菱智新能源", 0) == 1)
        check("标致508不再重复 (出现1次)", model_counts_6.get("标致508", 0) == 1)
        check("去重后车型数 == total，且没有任何车型重复",
              len(model_counts_6) == PAGE6_TOTAL and all(v == 1 for v in model_counts_6.values()))
    check("第10/11页各被重抓了一次 (1次初抓 + 1次修复重抓 = 2次)",
          call_counts_6.get(10) == 2 and call_counts_6.get(11) == 2)
    check("其余页 (如第1页) 只抓了1次，没有被无谓地重抓",
          call_counts_6.get(1) == 1)

    integrity_entries_6 = report_6.get("pagination_integrity", [])
    check("report['pagination_integrity'] 留了一条记录", len(integrity_entries_6) == 1)
    if integrity_entries_6:
        entry_6 = integrity_entries_6[0]
        check("留痕记录状态是 'fixed'", entry_6["status"] == "fixed")
        check("留痕记录里补回车型包含菱智新能源", "菱智新能源" in entry_6.get("recovered_models", []))
    check("report['failures'] 里没有新增失败记录 (修复成功，不算失败)", report_6["failures"] == [])

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
