#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试："价位地图"用的售价采集与累积式合并。
覆盖：
  1. parse_style_like_table 对「售价（万元）」列四种情况的解析：
     "6.48 - 9.48"（区间）/ "19.98"（单一价格）/ "0.00 - 0.00"（停产，视为无价格）/
     整列缺失（数据源改版，不应让解析整体失败）。
  2. merge_price_updates 合并规则（核心，写错了会丢数据）：
       - 已有真实价格的车型，本次解析不到价格 -> 不被覆盖，连 asof 都不动。
       - 已有 null 占位的车型，本次拿到真实价格 -> 被正确覆盖。
       - 本次完全没出现的车型 -> 原样保留。
  3. load_prices_file / write_prices_file 的基本行为（文件不存在、损坏、原子写格式）。
全部基于 sync_script.py 里实际会跑的函数，不发任何网络请求，也不真的抓数据源。
"""

import json
import os
import shutil
import sys

# 注意：tests/ 目录下其余测试文件里的 `sys.path.insert(0, .../__file__ 所在目录)` 这行
# 实际指向 tests/ 自己，而 sync_script.py 现在在 src/ 下，直接照抄会 ModuleNotFoundError。
# 这里改成指向 src/ 才能真的 import 到。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import sync_script as S

FAILURES = []
SCRATCH_ROOT = "/tmp/p1-sync/test_prices_scratch"


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def make_style_table_html(rows, with_price_col=True):
    """
    构造一个最小的「排名/车型/销量/厂商/售价（万元）/车型相关」表格 HTML，
    喂给 parse_style_like_table。rows 是 [(rank, model, sales, manufacturer, price_raw), ...]。
    with_price_col=False 时整列不出现，用来模拟数据源改版丢列的情况。
    """
    headers = ["排名", "车型", "销量", "厂商"]
    if with_price_col:
        headers.append("售价（万元）")
    headers.append("车型相关")

    def esc(v):
        return "" if v is None else str(v)

    parts = ["<table><thead><tr>"]
    for h in headers:
        parts.append(f"<th>{h}</th>")
    parts.append("</tr></thead><tbody>")
    for rank, model, sales, manufacturer, price_raw in rows:
        cells = [rank, model, sales, manufacturer]
        if with_price_col:
            cells.append(price_raw)
        cells.append("详情")
        parts.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def reset_scratch():
    if os.path.exists(SCRATCH_ROOT):
        shutil.rmtree(SCRATCH_ROOT)
    os.makedirs(SCRATCH_ROOT)


def main():
    print("=== 1. parse_style_like_table：售价列四种情况 ===")

    html = make_style_table_html([
        (1, "星愿", 30000, "长安福特", "6.48 - 9.48"),
        (2, "宋PLUS新能源", 25000, "比亚迪", "19.98"),
        (3, "领克20", 100, "领克", "0.00 - 0.00"),
    ])
    rows = S.parse_style_like_table(html)
    check("解析出 3 行", len(rows) == 3)
    by_model = {r["model"]: r for r in rows}

    r1 = by_model.get("星愿")
    check("区间价格 '6.48 - 9.48' -> price_lo == 6.48", r1 and r1["price_lo"] == 6.48)
    check("区间价格 '6.48 - 9.48' -> price_hi == 9.48", r1 and r1["price_hi"] == 9.48)

    r2 = by_model.get("宋PLUS新能源")
    check("单一价格 '19.98' -> price_lo == price_hi == 19.98",
          r2 and r2["price_lo"] == 19.98 and r2["price_hi"] == 19.98)

    r3 = by_model.get("领克20")
    check("'0.00 - 0.00' -> price_lo is None", r3 and r3["price_lo"] is None)
    check("'0.00 - 0.00' -> price_hi is None", r3 and r3["price_hi"] is None)

    print()
    print("=== 2. parse_style_like_table：售价列整体缺失（数据源改版） ===")
    html_no_price = make_style_table_html(
        [(1, "星愿", 30000, "长安福特", None)], with_price_col=False
    )
    rows_no_price = S.parse_style_like_table(html_no_price)
    check("列缺失时销量数据仍然解析出来（不因为价格列崩掉整个解析）", len(rows_no_price) == 1)
    if rows_no_price:
        check("列缺失时 price_lo 是 None", rows_no_price[0]["price_lo"] is None)
        check("列缺失时 price_hi 是 None", rows_no_price[0]["price_hi"] is None)
        check("列缺失时其余字段（model/sales/manufacturer/rank）仍然正确",
              rows_no_price[0]["model"] == "星愿"
              and rows_no_price[0]["sales"] == 30000
              and rows_no_price[0]["manufacturer"] == "长安福特"
              and rows_no_price[0]["rank"] == 1)

    print()
    print("=== 3. _parse_price_range 单独补充几个边界情况 ===")
    check("空字符串 -> (None, None)", S._parse_price_range("") == (None, None))
    check("纯 NaN -> (None, None)", S._parse_price_range(float("nan")) == (None, None))
    check("非数字字符串 -> (None, None)", S._parse_price_range("面议") == (None, None))
    check("None -> (None, None)", S._parse_price_range(None) == (None, None))

    print()
    print("=== 4. merge_price_updates：已有真实价格，本次解析不到 -> 不被覆盖 ===")
    existing = {
        "星愿": {"lo": 6.48, "hi": 9.48, "asof": "2026-08-01"},
    }
    price_rows = [
        {"model": "星愿", "price_lo": None, "price_hi": None},
    ]
    merged, added, updated = S.merge_price_updates(existing, price_rows, "2026-09-18")
    check("星愿的 lo 保持 6.48 不变", merged["星愿"]["lo"] == 6.48)
    check("星愿的 hi 保持 9.48 不变", merged["星愿"]["hi"] == 9.48)
    check("星愿的 asof 保持原样，没有被刷新成今天", merged["星愿"]["asof"] == "2026-08-01")
    check("这种情况不计入 added", added == 0)
    check("这种情况不计入 updated（因为实际没有任何改动）", updated == 0)

    print()
    print("=== 5. merge_price_updates：已有 null 占位，本次拿到真实价格 -> 被正确覆盖 ===")
    existing2 = {
        "宋PLUS新能源": {"lo": None, "hi": None, "asof": "2026-08-01"},
    }
    price_rows2 = [
        {"model": "宋PLUS新能源", "price_lo": 19.98, "price_hi": 19.98},
    ]
    merged2, added2, updated2 = S.merge_price_updates(existing2, price_rows2, "2026-09-18")
    check("null 占位被真实价格覆盖：lo == 19.98", merged2["宋PLUS新能源"]["lo"] == 19.98)
    check("null 占位被真实价格覆盖：hi == 19.98", merged2["宋PLUS新能源"]["hi"] == 19.98)
    check("asof 刷新为今天", merged2["宋PLUS新能源"]["asof"] == "2026-09-18")
    check("这种情况计入 updated", updated2 == 1)
    check("这种情况不计入 added（车型之前已经存在，哪怕值是 null）", added2 == 0)

    print()
    print("=== 6. merge_price_updates：本次完全没出现的车型 -> 原样保留 ===")
    existing3 = {
        "星愿": {"lo": 6.48, "hi": 9.48, "asof": "2026-08-01"},
        "领克20": {"lo": None, "hi": None, "asof": "2026-08-01"},
    }
    # 复现背景里描述的真实场景：2026-08 抓到过「领克20」，这次重抓同一个榜单它从榜单上
    # 消失了（数据源的已知缺陷）——price_rows 里压根不会出现"领克20"这个 model。
    price_rows3 = [
        {"model": "星愿", "price_lo": 6.48, "price_hi": 9.48},
    ]
    merged3, added3, updated3 = S.merge_price_updates(existing3, price_rows3, "2026-09-18")
    check("本次没出现的「领克20」原样保留在合并结果里", "领克20" in merged3)
    check("「领克20」的值完全没变", merged3["领克20"] == {"lo": None, "hi": None, "asof": "2026-08-01"})

    print()
    print("=== 7. merge_price_updates：全新车型（之前完全没记录过） -> 计入 added ===")
    merged4, added4, updated4 = S.merge_price_updates(
        {}, [{"model": "小米SU7", "price_lo": 21.59, "price_hi": 29.99}], "2026-09-18"
    )
    check("全新车型进入 merged", merged4.get("小米SU7") == {"lo": 21.59, "hi": 29.99, "asof": "2026-09-18"})
    check("全新车型计入 added", added4 == 1)
    check("全新车型不计入 updated", updated4 == 0)

    print()
    print("=== 8. merge_price_updates：全新车型但本次没有价格 -> 落 null 占位，也计入 added ===")
    merged5, added5, updated5 = S.merge_price_updates(
        {}, [{"model": "某停产车", "price_lo": None, "price_hi": None}], "2026-09-18"
    )
    check("null 占位也会被写入", merged5.get("某停产车") == {"lo": None, "hi": None, "asof": "2026-09-18"})
    check("计入 added", added5 == 1)

    print()
    print("=== 8b. merge_price_updates：价格没变 -> 完全不动，asof 不刷新 ===")
    existing6 = {"星愿": {"lo": 6.48, "hi": 9.48, "asof": "2026-08-01"}}
    merged6, added6, changed6 = S.merge_price_updates(
        existing6,
        [{"model": "星愿", "price_lo": 6.48, "price_hi": 9.48}],
        "2026-09-18",
    )
    check("价格相同时 asof 保持原值，不被刷成今天",
          merged6["星愿"] == {"lo": 6.48, "hi": 9.48, "asof": "2026-08-01"})
    check("价格没变不计入 changed", changed6 == 0)
    check("价格没变不计入 added", added6 == 0)

    print()
    print("=== 8c. merge_price_updates：价格真的变了 -> 覆盖并刷新 asof，计入 changed ===")
    merged7, added7, changed7 = S.merge_price_updates(
        {"星愿": {"lo": 6.48, "hi": 9.48, "asof": "2026-08-01"}},
        [{"model": "星愿", "price_lo": 5.98, "price_hi": 9.48}],
        "2026-09-18",
    )
    check("调价后写入新值并刷新 asof",
          merged7["星愿"] == {"lo": 5.98, "hi": 9.48, "asof": "2026-09-18"})
    check("调价计入 changed", changed7 == 1)

    print()
    print("=== 8d. _parse_price_range：只有一端是 0 也当作无售价 ===")
    check("0.00 - 9.48 -> (None, None)", S._parse_price_range("0.00 - 9.48") == (None, None))
    check("9.48 - 0.00 -> (None, None)", S._parse_price_range("9.48 - 0.00") == (None, None))

    print()
    print("=== 9. load_prices_file / write_prices_file：文件不存在、损坏、原子写格式 ===")
    reset_scratch()
    missing_path = os.path.join(SCRATCH_ROOT, "does_not_exist.json")
    loaded_missing = S.load_prices_file(missing_path)
    check("文件不存在时返回空表 {'models': {}}", loaded_missing == {"models": {}})

    corrupt_path = os.path.join(SCRATCH_ROOT, "corrupt.json")
    with open(corrupt_path, "w", encoding="utf-8") as f:
        f.write("{not valid json,,,")
    loaded_corrupt = S.load_prices_file(corrupt_path)
    check("文件损坏时不抛异常，返回空表", loaded_corrupt == {"models": {}})

    out_path = os.path.join(SCRATCH_ROOT, "prices.json")
    S.write_prices_file(
        {"星愿": {"lo": 6.48, "hi": 9.48, "asof": "2026-09-18"},
         "宋PLUS新能源": {"lo": None, "hi": None, "asof": "2026-09-18"}},
        "2026-09-18",
        out_path,
    )
    check("写完文件存在", os.path.exists(out_path))
    with open(out_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    check("中文车型名没有被转义成 \\uXXXX（ensure_ascii=False 生效）", "星愿" in raw_text)
    reloaded = json.loads(raw_text)
    check("updated_at 字段写对了", reloaded.get("updated_at") == "2026-09-18")
    check("models 字段结构写对了", reloaded.get("models", {}).get("星愿") == {"lo": 6.48, "hi": 9.48, "asof": "2026-09-18"})
    check("没有残留临时文件 .tmp", not os.path.exists(out_path + ".tmp"))

    # 再写一次，确认是原子替换（先写临时文件再 os.replace），不是原地追加/截断损坏旧内容
    S.write_prices_file({"新车型": {"lo": 1.0, "hi": 2.0, "asof": "2026-09-19"}}, "2026-09-19", out_path)
    with open(out_path, "r", encoding="utf-8") as f:
        reloaded2 = json.load(f)
    check("第二次写入完全替换了 models 内容（累积式合并的职责在 merge_price_updates，不在这里）",
          reloaded2.get("models") == {"新车型": {"lo": 1.0, "hi": 2.0, "asof": "2026-09-19"}})

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
