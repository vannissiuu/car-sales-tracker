#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端小样测试：用真实 HTML 样本模拟"抓完一个月" 之后的合并流程
（不发真实网络请求 —— 容器访问不了 16888，这里直接复用离线样本文件已经拿到的行）。

分两部分：
  PART A. 用真实 HTML 样本跑一遍完整流水线（读表 -> build_body_type_map -> label_rows -> 写CSV）。
  PART B. 针对这次 bug 修复（厂商名写法不一致导致关联键失效）专门构造的合成场景，
          直接单测 build_body_type_map / label_rows / reconcile_categories 三个纯函数：
            1. 同一车型在主榜和 body 榜里厂商名写法不同 -> 按车型名关联，应该能匹配上
               （这是本次修复的核心）。
            2. 车型不在任何 body 榜里 -> body_type 应该填"其他"，不是空字符串。
            3. body 榜单里的车型没有全部出现在本月输出行里 -> reconcile_categories 应该报出对账不平，
               并列出差异车型。

PART A 覆盖范围说明（诚实标注，不夸大）：
  - 主榜: 用样本里仅有的第1页 + 第2页 (100/553 行)，模拟"这个月主榜只有2页"的情况。
  - SUV / MPV: 各自只有第1页样本 (50/291, 50/52 行)，模拟"这个月这两个车体类型各自只有1页"。
  - 新能源: 只有第1页样本 (50/245 行)。
  - body-1/2/4/6/7/8: 没有样本，本测试里视为"未探测到"，对应车型的 body_type 会是"其他"——
    这正是规格要求的"没匹配上就填其他"的路径，也一并测到了。
  - 这不是"完整一个月553条全部拿到"的测试，而是验证"抓到的行 -> 打标签 -> 写CSV"这条
    流水线本身是对的；分页循环本身（total -> total_pages -> 逐页拉取）在 test_parser.py
    里已经用第1页 total=553 与第2页 rank=51 交叉验证过分页是连续的，没有另外重复造假数据测。
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

SAMPLE_DIR = "/tmp/cv3/probe_v3_output"

# label_rows() 这一轮改动加了 mapping 参数（品牌解析用）。这里这些测试不关心品牌，
# 用一个空字典即可 —— 两个映射表都是空的，resolve_brand 会稳定回退成 manufacturer 原值，
# 不影响 body_type/energy_type 相关的既有断言。
EMPTY_BRAND_MAPPING = {"manufacturer_to_brand": {}, "model_to_brand": {}}
OUT_DIR = "/tmp/p1-sync/mini_output"
OUT_CSV = os.path.join(OUT_DIR, "sales_mini.csv")


def read_sample(name):
    with open(os.path.join(SAMPLE_DIR, name), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1) 主榜：拼接样本里的第1页 + 第2页
    style_rows = (
        S.parse_style_like_table(read_sample("01_style_202401_p1_raw.html"))
        + S.parse_style_like_table(read_sample("02_style_202401_p2_raw.html"))
    )
    assert len(style_rows) == 100, f"期望 100 行主榜，实际 {len(style_rows)}"

    # 2) body 类型：只用 body-5(SUV) 和 body-3(MPV) 各自的第1页样本
    body_lists = []
    for body_id, sample_name, expected_name in [
        (5, "05_body5_suv_202401_p1_raw.html", "SUV"),
        (3, "06_body3_mpv_202401_p1_raw.html", "MPV"),
    ]:
        html = read_sample(sample_name)
        name = S.extract_body_type_name(html)
        assert name == expected_name, f"{sample_name} 提取到的分类名是 {name}，期望 {expected_name}"
        rows = S.parse_style_like_table(html)
        body_lists.append((body_id, name, rows))

    # 用生产代码里实际会跑的 build_body_type_map（车型名关联，非 (model,manufacturer)）
    body_type_map, conflicts, _cross = S.build_body_type_map(body_lists)
    assert not conflicts, f"不应该有关联冲突，实际: {conflicts}"

    # 3) 新能源集合：只用 ev 第1页样本，同样用生产代码的 build_ev_set（车型名集合）
    ev_rows = S.parse_style_like_table(read_sample("07_ev_202401_p1_raw.html"))
    ev_set = S.build_ev_set(ev_rows)

    # 4) 用 sync_script 里实际会跑的合并逻辑打标签（和生产代码同一个函数，不是重写一份）
    out_rows, unmatched = S.label_rows(style_rows, body_type_map, ev_set, EMPTY_BRAND_MAPPING, 2024, 1)

    print(f"主榜行数: {len(style_rows)}")
    print(f"body_type_map 覆盖车型数: {len(body_type_map)}")
    print(f"ev_set 覆盖车型数: {len(ev_set)}")
    print(f"输出行数: {len(out_rows)}")
    print(f"未匹配到 body_type 的行数: {unmatched}")
    print(f"标记为新能源的行数: {sum(1 for r in out_rows if r['energy_type'] == '新能源')}")

    # 抽查几个已知事实，防止标签打错
    checks = []

    def find_row(model):
        for r in out_rows:
            if r["model"] == model:
                return r
        return None

    r = find_row("宋PLUS新能源")
    checks.append(("宋PLUS新能源 排名第3，应该在 ev 样本第1页里 (rank<=50)，energy_type=新能源",
                    r is not None and r["energy_type"] == "新能源"))

    r = find_row("长安CS75PLUS")
    checks.append(("长安CS75PLUS 是 SUV 车型 (在 body-5 样本第1页里)，body_type=SUV 且非新能源",
                    r is not None and r["body_type"] == "SUV" and r["energy_type"] == "燃油"))

    r = find_row("朗逸")
    checks.append(("朗逸 排名第2，主榜前2页里，但不在 body-5/body-3/ev 任何一个样本里 -> body_type 应为「其他」(不是空字符串)",
                    r is not None and r["body_type"] == "其他" and r["energy_type"] == "燃油"))

    ok = True
    for desc, cond in checks:
        status = "PASS" if cond else "FAIL"
        print(f"[{status}] {desc}")
        ok = ok and cond

    # 5) 写迷你 CSV（按销量降序，和生产脚本 write_outputs 的排序规则一致）
    out_rows_sorted = sorted(out_rows, key=lambda r: (r["year"], r["month"], -r["sales"]))
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=S.CSV_FIELDS)
        writer.writeheader()
        for r in out_rows_sorted:
            writer.writerow({k: r[k] for k in S.CSV_FIELDS})
    print(f"\n写入 {OUT_CSV} ({len(out_rows_sorted)} 行)")

    print()
    print("=" * 60)
    print("PART B: 合成场景 —— 针对本次 bug 修复的专项断言")
    print("=" * 60)
    ok_b = run_part_b()

    if not (ok and ok_b):
        sys.exit(1)


def run_part_b():
    ok = True

    # ------------------------------------------------------------------
    # 断言 1（核心修复）：同一车型，主榜和 body 榜里厂商名写法不一致，
    # 用 model-only 关联应该照样能匹配上。
    # ------------------------------------------------------------------
    print()
    print("--- 断言 1: 厂商名写法不一致时，按车型名关联应能匹配上 ---")
    style_rows_b1 = [
        {"rank": 1, "model": "MG4", "manufacturer": "上汽集团", "sales": 5000},
    ]
    # body-1 (两厢车) 榜单里同一台车，厂商名写法不同（"上汽MG" vs 主榜的"上汽集团"）
    body_lists_b1 = [
        (1, "两厢车", [{"rank": 1, "model": "MG4", "manufacturer": "上汽MG", "sales": 5000}]),
    ]
    body_type_map_b1, conflicts_b1, _cross_b1 = S.build_body_type_map(body_lists_b1)
    ev_set_b1 = S.build_ev_set([])
    out_rows_b1, other_b1 = S.label_rows(style_rows_b1, body_type_map_b1, ev_set_b1, EMPTY_BRAND_MAPPING, 2024, 1)

    r = out_rows_b1[0]
    # body-1 的原始分类名是"两厢车"，映射成用户口径的最终类别后应该是"轿车"
    # （两厢车/三厢车合并进轿车，是这一轮改动新加的映射规则）。
    cond1 = (r["body_type"] == "轿车") and (r["manufacturer"] == "上汽集团")
    status = "PASS" if cond1 else "FAIL"
    print(f"[{status}] MG4 在主榜厂商名是「上汽集团」、body-1(两厢车)榜厂商名是「上汽MG」，"
          f"仍应关联出 body_type=轿车(两厢车映射后) —— 实际: body_type={r['body_type']!r}, "
          f"manufacturer(取自主榜)={r['manufacturer']!r}, other_count={other_b1}")
    ok = ok and cond1
    cond1b = (other_b1 == 0) and (not conflicts_b1)
    status = "PASS" if cond1b else "FAIL"
    print(f"[{status}] 这种情况不应该被算成「其他」，也不应该触发冲突告警 "
          f"(other_count={other_b1}, conflicts={conflicts_b1})")
    ok = ok and cond1b

    # ------------------------------------------------------------------
    # 断言 2：车型不在任何 body 榜里 -> body_type 填「其他」，不是空字符串。
    # ------------------------------------------------------------------
    print()
    print("--- 断言 2: 车型不在任何 body 榜单里 -> body_type 应为「其他」(非空) ---")
    style_rows_b2 = [
        {"rank": 1, "model": "五菱宏光", "manufacturer": "上汽通用五菱", "sales": 8000},
    ]
    body_lists_b2 = [
        (5, "SUV", [{"rank": 1, "model": "长安CS75PLUS", "manufacturer": "长安汽车", "sales": 9000}]),
    ]
    body_type_map_b2, _conflicts_b2, _cross_b2 = S.build_body_type_map(body_lists_b2)
    out_rows_b2, other_b2 = S.label_rows(style_rows_b2, body_type_map_b2, set(), EMPTY_BRAND_MAPPING, 2024, 1)
    r = out_rows_b2[0]
    cond2 = (r["body_type"] == "其他") and (r["body_type"] != "") and (other_b2 == 1)
    status = "PASS" if cond2 else "FAIL"
    print(f"[{status}] 五菱宏光不在 SUV 榜里 -> body_type={r['body_type']!r} "
          f"(应为 '其他'，不是空字符串), other_count={other_b2}")
    ok = ok and cond2

    # ------------------------------------------------------------------
    # 断言 3：body-1(两厢车) 榜单实际抓到 3 个车型 (A/B/C)，但本月主榜(style_rows)
    # 只包含其中 2 个 (A/B)——车型C 这个月没在主榜出现（数据源自身的不一致，真实可能发生）
    # -> reconcile_categories 应该报出「轿车」对账不平，并把差异车型 C 列出来。
    # 关于"declared总条数 vs 实际解析行数"这类分页完整性问题，现在由 body_probe 表格
    # （fetch_full_listing 内部已有的告警日志）覆盖，不再是 reconcile_categories 的职责——
    # 这个函数现在只关心"body 榜单实际抓到的车型集合"和"最终输出行"是否对得上。
    # ------------------------------------------------------------------
    print()
    print("--- 断言 3: body 榜单里的车型没有全部出现在本月输出行里 -> 对账应触发告警 ---")
    body_page_rows_b3 = [
        {"rank": 1, "model": "车型A", "manufacturer": "厂商X", "sales": 100},
        {"rank": 2, "model": "车型B", "manufacturer": "厂商X", "sales": 90},
        {"rank": 3, "model": "车型C", "manufacturer": "厂商Y", "sales": 80},
    ]
    body_lists_b3 = [(1, "两厢车", body_page_rows_b3)]
    body_type_map_b3, conflicts_b3, _cross_b3 = S.build_body_type_map(body_lists_b3)

    # 本月主榜只有 A、B（车型C 缺席），外加一个不在任何 body 榜的车型D
    style_rows_b3 = [
        {"rank": 1, "model": "车型A", "manufacturer": "厂商X", "sales": 100},
        {"rank": 2, "model": "车型B", "manufacturer": "厂商X", "sales": 90},
        {"rank": 3, "model": "车型D(其他)", "manufacturer": "厂商Z", "sales": 10},
    ]
    out_rows_b3, other_b3 = S.label_rows(style_rows_b3, body_type_map_b3, set(), EMPTY_BRAND_MAPPING, 2024, 1)

    recon_b3 = S.reconcile_categories(out_rows_b3, body_lists_b3)

    result = recon_b3["results"][0]
    cond3 = (
        result["final_category"] == "轿车"
        and result["mismatch"] is True
        and result["page_side"] == 3  # body-1 榜单实际抓到 A/B/C 三个车型
        and result["actual"] == 2     # 本月输出里只有 A/B 被标成轿车
        and result["diff_models"] == ["车型C"]
    )
    status = "PASS" if cond3 else "FAIL"
    print(f"[{status}] 轿车对账结果: page_side={result['page_side']}, actual={result['actual']}, "
          f"mismatch={result['mismatch']}, diff_models={result['diff_models']} "
          f"(期望 page_side=3, actual=2, mismatch=True, diff_models=['车型C'])")
    ok = ok and cond3

    cond3b = recon_b3["total_check"]["ok"] is True  # 2(轿车)+1(其他)==3行(out_rows里实际有的行数)，应该平
    status = "PASS" if cond3b else "FAIL"
    print(f"[{status}] 总量对账应仍然平（各分类之和+其他 == 主榜总行数）: {recon_b3['total_check']}")
    ok = ok and cond3b

    return ok


if __name__ == "__main__":
    main()
