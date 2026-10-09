#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试："能源标注"人工裁定覆盖表 + 常驻疑点告警。
覆盖：
  1. resolve_energy：无覆盖时按 ev_set 默认规则；覆盖值「新能源」/「燃油」能分别战胜
     默认规则；非法覆盖值（"纯电"、""、None、数字）一律被忽略、退回默认规则，不会把非法值
     写进 energy_type；mapping 里压根没有 model_to_energy 键时不报错。
  2. normalize_energy_column：只改写覆盖表命中且取值不同的行；未命中的行逐字段原样返回；
     幂等（第二次 changed_count == 0）；不原地修改传入的 dict。
  3. detect_suspect_energy_labels：能报出纯新能源品牌的燃油行、名字带 PHEV 的燃油行；
     不报「风行T5 EVO」「EVOS」（EVO 误报防护）；不报已在 model_to_energy 里的车型（两种取值
     都试）和 energy_type 为「新能源」的行；跨月聚合的累计销量/月数/首末月正确。
全部基于 sync_script.py 里实际会跑的纯函数，车型名均为虚构，不发任何网络请求。
"""

import os
import sys

# 指向 src/ 才能 import 到 sync_script（同 test_prices.py）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sync_script as S

FAILURES = []


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def row(year, month, brand, model, energy_type, sales):
    return {
        "year": year, "month": month, "manufacturer": "虚构厂商", "brand": brand,
        "model": model, "body_type": "SUV", "energy_type": energy_type, "sales": sales,
    }


def main():
    ev_set = {"虚构甲 EV"}

    # ---- 1. resolve_energy ----
    check("无覆盖 + 在 ev_set 里 -> 新能源", S.resolve_energy("虚构甲 EV", ev_set, {}) == "新能源")
    check("无覆盖 + 不在 ev_set 里 -> 燃油", S.resolve_energy("虚构乙", ev_set, {}) == "燃油")
    check("覆盖值「新能源」战胜「不在 ev_set 里」",
          S.resolve_energy("虚构乙", ev_set, {"model_to_energy": {"虚构乙": "新能源"}}) == "新能源")
    check("覆盖值「燃油」战胜「在 ev_set 里」",
          S.resolve_energy("虚构甲 EV", ev_set, {"model_to_energy": {"虚构甲 EV": "燃油"}}) == "燃油")

    for bad in ["纯电", "", None, 1, 0, True, ["新能源"]]:
        m = {"model_to_energy": {"虚构甲 EV": bad, "虚构乙": bad}}
        ok = (S.resolve_energy("虚构甲 EV", ev_set, m) == "新能源"
              and S.resolve_energy("虚构乙", ev_set, m) == "燃油")
        check(f"非法覆盖值 {bad!r} 被忽略，退回 ev_set 默认规则", ok)

    check("mapping 没有 model_to_energy 键时不报错，走默认规则",
          S.resolve_energy("虚构甲 EV", ev_set, {"model_to_brand": {}}) == "新能源"
          and S.resolve_energy("虚构乙", ev_set, {"model_to_brand": {}}) == "燃油")
    check("model_to_energy 为 None 时不报错，走默认规则",
          S.resolve_energy("虚构乙", ev_set, {"model_to_energy": None}) == "燃油")

    # ---- 2. normalize_energy_column ----
    mapping = {"model_to_energy": {"虚构丙": "新能源", "虚构丁": "燃油", "虚构戊": "纯电"}}
    r_hit = row(2026, 6, "虚构品牌", "虚构丙", "燃油", 100)          # 命中且不同 -> 改写
    r_same = row(2026, 6, "虚构品牌", "虚构丁", "燃油", 200)         # 命中但已一致 -> 不动
    r_miss_ev = row(2026, 6, "虚构品牌", "虚构己", "新能源", 300)    # 未命中 -> 不动
    r_miss_ice = row(2026, 6, "虚构品牌", "虚构庚", "燃油", 400)     # 未命中 -> 不动
    r_bad = row(2026, 6, "虚构品牌", "虚构戊", "燃油", 500)          # 命中但覆盖值非法 -> 忽略
    rows = [r_hit, r_same, r_miss_ev, r_miss_ice, r_bad]
    snapshot = [dict(r) for r in rows]

    out, changed = S.normalize_energy_column(rows, mapping)
    check("normalize_energy_column 只改写覆盖表命中且取值不同的行（changed == 1）", changed == 1)
    check("命中行被改写成裁定值", out[0]["energy_type"] == "新能源")
    check("命中但已一致的行 energy_type 不变", out[1]["energy_type"] == "燃油")
    check("非法覆盖值对应的行不被改写", out[4]["energy_type"] == "燃油")
    check("未命中的行逐字段原样返回", [out[2], out[3]] == [snapshot[2], snapshot[3]])
    check("命中行除 energy_type 外其它字段不变",
          {k: v for k, v in out[0].items() if k != "energy_type"}
          == {k: v for k, v in snapshot[0].items() if k != "energy_type"})
    check("返回行数与输入一致、顺序不变", [r["model"] for r in out] == [r["model"] for r in snapshot])

    out2, changed2 = S.normalize_energy_column(out, mapping)
    check("幂等：第二次跑 changed_count == 0", changed2 == 0)
    check("幂等：第二次结果与第一次一致", out2 == out)

    check("不原地修改传入的 dict（原 dict 对象 energy_type 仍是旧值）", r_hit["energy_type"] == "燃油")
    check("传入的全部 dict 未被修改", rows == snapshot)
    check("改写的行是新 dict 对象", out[0] is not r_hit)

    out3, changed3 = S.normalize_energy_column(rows, {})
    check("mapping 没有 model_to_energy 键时不报错、不改任何行", changed3 == 0 and out3 == snapshot)

    # ---- 3. detect_suspect_energy_labels ----
    nev_brand_row = row(2026, 5, "零跑", "虚构辛", "燃油", 1000)
    name_row = row(2026, 5, "虚构品牌", "虚构壬 PHEV", "燃油", 800)
    s = S.detect_suspect_energy_labels([nev_brand_row, name_row], {})
    by_model = {x["model"]: x for x in s}
    check("报出纯新能源品牌下的燃油行，reason 正确",
          by_model.get("虚构辛", {}).get("reason") == "品牌为纯新能源品牌")
    check("报出名字带 PHEV 的燃油行，reason 正确",
          by_model.get("虚构壬 PHEV", {}).get("reason") == "车型名含新能源标记")

    evo_rows = [
        row(2026, 5, "风行", "风行T5 EVO", "燃油", 900),
        row(2026, 5, "虚构品牌", "EVOS", "燃油", 700),
    ]
    check("不报「风行T5 EVO」和「EVOS」（EVO 误报防护）",
          S.detect_suspect_energy_labels(evo_rows, {}) == [])
    check("独立的 EV 仍会被报出（字母边界断言没有过严）",
          [x["model"] for x in S.detect_suspect_energy_labels(
              [row(2026, 5, "虚构品牌", "虚构甲 EV", "燃油", 10)], {})] == ["虚构甲 EV"])

    suspects_rows = [nev_brand_row, name_row]
    for val in ["新能源", "燃油"]:
        m = {"model_to_energy": {"虚构辛": val, "虚构壬 PHEV": val}}
        check(f"已在 model_to_energy 里的车型不报（裁定值 {val}）",
              S.detect_suspect_energy_labels(suspects_rows, m) == [])
    check("只裁定其中一个，另一个仍然报出",
          [x["model"] for x in S.detect_suspect_energy_labels(
              suspects_rows, {"model_to_energy": {"虚构辛": "燃油"}})] == ["虚构壬 PHEV"])
    check("energy_type 为「新能源」的行不报",
          S.detect_suspect_energy_labels([row(2026, 5, "零跑", "虚构辛", "新能源", 1000)], {}) == [])
    check("普通燃油车（非纯新能源品牌、名字无标记）不报",
          S.detect_suspect_energy_labels([row(2026, 5, "虚构品牌", "虚构乙", "燃油", 1000)], {}) == [])

    agg_rows = [
        row(2026, 6, "零跑", "虚构辛", "燃油", 300),
        row(2026, 4, "零跑", "虚构辛", "燃油", 100),
        row(2026, 5, "零跑", "虚构辛", "燃油", 200),
        row(2026, 5, "零跑", "虚构辛", "新能源", 9999),   # 新能源行不计入
        row(2026, 6, "虚构品牌", "虚构壬 PHEV", "燃油", 1000),
    ]
    agg = S.detect_suspect_energy_labels(agg_rows, {})
    check("按累计销量降序（虚构壬 PHEV 1000 在前，虚构辛 600 在后）",
          [x["model"] for x in agg] == ["虚构壬 PHEV", "虚构辛"])
    x = [i for i in agg if i["model"] == "虚构辛"][0]
    check("跨月聚合：累计销量 == 600（不含新能源行）", x["sales"] == 600)
    check("跨月聚合：月数 == 3", x["months"] == 3)
    check("跨月聚合：首月 2026-04、末月 2026-06", x["first"] == "2026-04" and x["last"] == "2026-06")
    check("每项都带 brand/model/sales/months/first/last/reason 七个字段",
          all(set(i) == {"brand", "model", "sales", "months", "first", "last", "reason"} for i in agg))
    check("传入的 rows 未被修改", agg_rows[0]["energy_type"] == "燃油" and agg_rows[3]["sales"] == 9999)

    # ---- 14. NEV_NAME_PATTERN 的真实车名回归 ----
    # 这一组全部是真实车型名，不是虚构名：它们每一个都对应一次实际踩过的坑或一次实际的误报风险，
    # 改动 NEV_NAME_PATTERN 之前先让这组过。
    # 「ID.ERA 5S」是漏报过的真实案例：它是上汽大众的插混，但品牌「大众」不在 NEV_ONLY_BRANDS 里、
    # 名字里也不含 EV，加 ID\. 这一条之前，品牌规则和名字规则一个都抓不到它。
    # 「荣威i5」系列和「风行T5 EVO」「EVOS」是反向案例：它们是实打实的燃油车，误报了会让告警失去意义
    # （荣威i5 单款 16 万辆以上）。
    for name, expected in [
        ("ID.ERA 5S", True), ("探岳L PHEV", True), ("奔驰EQA", True),
        ("奥迪Q5 e-tron", True), ("宝马iX3", True),
        ("风行T5 EVO", False), ("EVOS", False),
        ("荣威i5", False), ("荣威i6", False), ("荣威iMAX8", False),
        ("凯美瑞双擎 HEV", False),   # 不插电的 HEV 按本项目口径算燃油，不该报
        ("大唐", False),             # 它靠品牌规则命中，不该靠名字规则
    ]:
        check(f"NEV_NAME_PATTERN: {name} 命中应为 {expected}",
              bool(S.NEV_NAME_PATTERN.search(name)) is expected)

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
