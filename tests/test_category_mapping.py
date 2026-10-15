#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：本轮改动的核心——
  1. 两厢车/三厢车 -> 轿车 的分类映射 + 优先级裁决 (SUV > MPV > 轿车 > 运动汽车)
  2. normalize_legacy_body_types 对存量数据的幂等原地规范化
  3. reconcile_categories_final 的对账：page_side 仍是并集去重口径；守门只看 3 条硬失败
     （榜单车型不在主榜里 / 分类证据丢失 / 总量对账），并集≠实际标上不再判失败
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


def to_models_by_month(ym, body_lists):
    """复刻 sync_month 存进 report['_body_list_models'] 的形状：只存车型名集合，不存整行。"""
    return {ym: [[bid, rn, sorted({r["model"] for r in rows})] for bid, rn, rows in body_lists]}


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
    recon_1 = S.reconcile_categories_final(out_rows_1, to_models_by_month("2024-01", body_lists_1))[0]
    sedan_result = next(r for r in recon_1["results"] if r["final_category"] == "轿车")
    check("轿车对账: page_side(并集, 朗逸只算1次)=1, actual=1, 无改判",
          sedan_result["page_side"] == 1 and sedan_result["actual"] == 1
          and sedan_result["reassigned"] == [] and sedan_result["added_by_backfill"] == [])
    check("无硬失败 (missing_from_main / evidence_lost 为空)",
          recon_1["missing_from_main"] == [] and recon_1["evidence_lost"] == [])
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
    recon_5 = S.reconcile_categories_final(out_rows_5, to_models_by_month("2024-01", body_lists_5))[0]
    sedan_result_5 = next(r for r in recon_5["results"] if r["final_category"] == "轿车")

    check(f"轿车对账 page_side (并集去重) == 8，不是 10 (实际: {sedan_result_5['page_side']})",
          sedan_result_5["page_side"] == 8)
    check(f"轿车对账 actual == 8 (本月主榜8个车型全都在) (实际: {sedan_result_5['actual']})",
          sedan_result_5["actual"] == 8)
    check("并集去重后 page_side == actual，无改判，无硬失败",
          sedan_result_5["reassigned"] == [] and sedan_result_5["added_by_backfill"] == []
          and recon_5["missing_from_main"] == [] and recon_5["evidence_lost"] == []
          and recon_5["total_check"]["ok"] is True)

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
    print("=== 断言 7: reconcile_categories_final —— 新对账不变式（3 条硬失败 + 信息性改判明细）===")

    def mk(model):
        return {"rank": 1, "model": model, "manufacturer": "厂商X", "sales": 100}

    def final_for(year, month, specs, body_lists):
        """specs: [(model, body_type), ...] 为该月最终定稿行；返回 (rows, models_by_month)。"""
        rows = [make_row(year, month, m, bt) for m, bt in specs]
        return rows, to_models_by_month(f"{year}-{month:02d}", body_lists)

    # 7.1 优先级落败者不判失败（复刻 2026-09 宏光MINIEV：body-1 两厢车 + body-4 运动汽车，最终轿车）
    bl = [
        (1, "两厢车", [mk("宏光MINIEV"), mk("朗逸")]),
        (4, "运动汽车", [mk("宏光MINIEV"), mk("Model3"), mk("保时捷911")]),
    ]
    rows71, mbm71 = final_for(2026, 9, [
        ("宏光MINIEV", "轿车"), ("朗逸", "轿车"), ("Model3", "运动汽车"), ("保时捷911", "运动汽车"),
    ], bl)
    rec71 = S.reconcile_categories_final(rows71, mbm71)
    check("7.1 返回 1 个月份，year/month 正确", len(rec71) == 1 and rec71[0]["year"] == 2026 and rec71[0]["month"] == 9)
    rec71 = rec71[0]
    sport71 = next(r for r in rec71["results"] if r["final_category"] == "运动汽车")
    check(f"7.1 运动汽车 page_side(3) 比 actual(2) 多 1 (实际 {sport71['page_side']}/{sport71['actual']})",
          sport71["page_side"] == 3 and sport71["actual"] == 2)
    check("7.1 宏光MINIEV 出现在运动汽车的 reassigned 里，landed_in == 轿车",
          sport71["reassigned"] == [{"model": "宏光MINIEV", "landed_in": "轿车"}])
    check("7.1 优先级落败不判失败: missing_from_main / evidence_lost 为空, total_check.ok 为真",
          rec71["missing_from_main"] == [] and rec71["evidence_lost"] == [] and rec71["total_check"]["ok"] is True)
    sedan71 = next(r for r in rec71["results"] if r["final_category"] == "轿车")
    check("7.1 轿车 page_side(2)==actual(2)，无改判", sedan71["page_side"] == 2 and sedan71["actual"] == 2
          and sedan71["reassigned"] == [] and sedan71["added_by_backfill"] == [])

    # 7.2 跨月回填救回者不判失败：本月在 body-1 榜单里，最终已是 SUV，但不在 SUV 榜并集里
    bl = [(1, "两厢车", [mk("漏网车型")]), (5, "SUV", [mk("长安CS75PLUS")])]
    rows72, mbm72 = final_for(2026, 9, [("漏网车型", "SUV"), ("长安CS75PLUS", "SUV")], bl)
    rec72 = S.reconcile_categories_final(rows72, mbm72)[0]
    suv72 = next(r for r in rec72["results"] if r["final_category"] == "SUV")
    check("7.2 漏网车型 出现在 SUV 的 added_by_backfill 里", suv72["added_by_backfill"] == ["漏网车型"])
    check("7.2 SUV page_side=1, actual=2", suv72["page_side"] == 1 and suv72["actual"] == 2)
    sedan72 = next(r for r in rec72["results"] if r["final_category"] == "轿车")
    check("7.2 漏网车型 同时出现在轿车的 reassigned 里 (landed_in=SUV)",
          sedan72["reassigned"] == [{"model": "漏网车型", "landed_in": "SUV"}])
    check("7.2 回填救回不判失败", rec72["missing_from_main"] == [] and rec72["evidence_lost"] == []
          and rec72["total_check"]["ok"] is True)

    # 7.3 硬失败 ①：榜单里有 X，该月输出行里没有 X
    bl = [(5, "SUV", [mk("车型X"), mk("车型A")])]
    rows73, mbm73 = final_for(2026, 9, [("车型A", "SUV")], bl)
    rec73 = S.reconcile_categories_final(rows73, mbm73)[0]
    check("7.3 硬失败①: missing_from_main 含 车型X", rec73["missing_from_main"] == ["车型X"])
    check("7.3 车型X 不在主榜里，不会同时被算成 reassigned",
          next(r for r in rec73["results"] if r["final_category"] == "SUV")["reassigned"] == [])
    check("7.3 车型X 没有行，也不算 evidence_lost", rec73["evidence_lost"] == [])

    # 7.4 硬失败 ②：榜单里有 Y，输出行里有 Y 但 body_type 是「其他」
    bl = [(1, "两厢车", [mk("车型Y")]), (4, "运动汽车", [mk("车型Y")])]
    rows74, mbm74 = final_for(2026, 9, [("车型Y", "其他")], bl)
    rec74 = S.reconcile_categories_final(rows74, mbm74)[0]
    check("7.4 硬失败②: evidence_lost 含 车型Y，且带上它出现的榜单",
          rec74["evidence_lost"] == [{"model": "车型Y", "body_lists": ["body-1(两厢车)", "body-4(运动汽车)"]}])
    check("7.4 车型Y 有行，不在 missing_from_main", rec74["missing_from_main"] == [])

    # 7.5 硬失败 ② 的反例：最终 body_type 是另一个真实类别 -> 不报失败
    for real in ("SUV", "MPV", "轿车", "运动汽车"):
        bl = [(1, "两厢车", [mk("车型Z")])]
        rows75, mbm75 = final_for(2026, 9, [("车型Z", real)], bl)
        rec75 = S.reconcile_categories_final(rows75, mbm75)[0]
        check(f"7.5 车型Z 在两厢车榜、最终是真实类别「{real}」-> 不报 evidence_lost / missing",
              rec75["evidence_lost"] == [] and rec75["missing_from_main"] == []
              and rec75["total_check"]["ok"] is True)

    # 7.6 总量对账：通过 / 不通过各一条（用不属于任何已知类别的 body_type 制造不平）
    bl = [(5, "SUV", [mk("车型A")])]
    rows76a, mbm76 = final_for(2026, 9, [("车型A", "SUV"), ("车型B", "其他")], bl)
    tc_ok = S.reconcile_categories_final(rows76a, mbm76)[0]["total_check"]
    check(f"7.6 总量对账通过: 1(SUV)+1(其他)==2 {tc_ok}",
          tc_ok == {"matched_sum": 1, "other_count": 1, "total_rows": 2, "ok": True})
    rows76b, _ = final_for(2026, 9, [("车型A", "SUV"), ("车型B", "其他"), ("车型C", "")], bl)
    tc_bad = S.reconcile_categories_final(rows76b, mbm76)[0]["total_check"]
    check(f"7.6 总量对账不通过: 1+1 != 3 (有行 body_type 为空串) {tc_bad}",
          tc_bad == {"matched_sum": 1, "other_count": 1, "total_rows": 3, "ok": False})

    # 7.7 多月份：每月独立算，互不污染
    bl_a = [(5, "SUV", [mk("车型A")])]
    bl_b = [(5, "SUV", [mk("车型B")])]
    rows77 = [
        make_row(2026, 8, "车型A", "SUV"), make_row(2026, 8, "车型B", "其他"),  # 8月：B 不在8月榜单，不应被查
        make_row(2026, 9, "车型A", "其他"), make_row(2026, 9, "车型B", "SUV"),  # 9月：A 不在9月榜单
    ]
    mbm77 = {}
    mbm77.update(to_models_by_month("2026-08", bl_a))
    mbm77.update(to_models_by_month("2026-09", bl_b))
    rec77 = S.reconcile_categories_final(rows77, mbm77)
    check("7.7 两个月份，按月份升序", [(r["year"], r["month"]) for r in rec77] == [(2026, 8), (2026, 9)])
    check("7.7 8月: 无硬失败 (9月的 车型A=其他 不污染8月)",
          rec77[0]["evidence_lost"] == [] and rec77[0]["missing_from_main"] == []
          and rec77[0]["total_check"] == {"matched_sum": 1, "other_count": 1, "total_rows": 2, "ok": True})
    check("7.7 9月: 无硬失败 (8月的 车型B=其他 不污染9月)",
          rec77[1]["evidence_lost"] == [] and rec77[1]["missing_from_main"] == []
          and rec77[1]["total_check"]["total_rows"] == 2)
    rows77b = [make_row(2026, 8, "车型A", "SUV"), make_row(2026, 9, "车型B", "其他")]
    rec77b = S.reconcile_categories_final(rows77b, mbm77)
    check("7.7 只有9月的车型B 落进「其他」 -> 只有9月报 evidence_lost",
          rec77b[0]["evidence_lost"] == []
          and [x["model"] for x in rec77b[1]["evidence_lost"]] == ["车型B"])

    # 7.8 空输入
    check("7.8 body_list_models_by_month 为空 dict -> 空 list，不抛异常",
          S.reconcile_categories_final([make_row(2026, 9, "车型A", "SUV")], {}) == [])
    check("7.8 rows 与 dict 都为空 -> 空 list", S.reconcile_categories_final([], {}) == [])
    rec78 = S.reconcile_categories_final([], {"2026-09": [[5, "SUV", ["车型A"]]]})
    check("7.8 有榜单、无输出行 -> 不抛异常，车型A 报 missing_from_main",
          len(rec78) == 1 and rec78[0]["missing_from_main"] == ["车型A"])

    # 7.9 纯函数：不修改传入对象
    import copy
    bl = [(1, "两厢车", [mk("宏光MINIEV")]), (4, "运动汽车", [mk("宏光MINIEV"), mk("Model3")])]
    rows79, mbm79 = final_for(2026, 9, [("宏光MINIEV", "轿车"), ("Model3", "其他")], bl)
    rows79_before, mbm79_before = copy.deepcopy(rows79), copy.deepcopy(mbm79)
    S.reconcile_categories_final(rows79, mbm79)
    check("7.9 纯函数: rows / body_list_models_by_month 调用前后完全相同",
          rows79 == rows79_before and mbm79 == mbm79_before)

    # 7.10 最多列 20 个，完整个数仍可得
    many = [f"缺失{i:03d}" for i in range(25)]
    rec710 = S.reconcile_categories_final(
        [make_row(2026, 9, "车型A", "SUV")],
        {"2026-09": [[5, "SUV", ["车型A"] + many]]})[0]
    check("7.10 missing_from_main 排序且最多 20 个，count 为完整个数 25",
          rec710["missing_from_main"] == sorted(many)[:20] and rec710["missing_from_main_count"] == 25)

    # 7.11 只靠跨月回填才有的类别也要计入总量对账（不能被它拉不平）
    rec711 = S.reconcile_categories_final(
        [make_row(2026, 9, "车型A", "MPV")], {"2026-09": [[5, "SUV", ["车型A"]]]})[0]
    mpv711 = next((r for r in rec711["results"] if r["final_category"] == "MPV"), None)
    check("7.11 MPV 无贡献榜单、全靠回填: 出现在 results，added_by_backfill=[车型A]，总量平",
          mpv711 is not None and mpv711["page_side"] == 0 and mpv711["added_by_backfill"] == ["车型A"]
          and rec711["total_check"]["ok"] is True)

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
