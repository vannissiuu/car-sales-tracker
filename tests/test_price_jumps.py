#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
专项测试：售价异动检测 detect_price_jumps（信息性，不计入质量守门）。
覆盖：
  1. 上限相对变化 >= 20% -> 命中（奥德赛 23.58~43.98 -> 18.48~32.88 的真实数据）。
  2. 相对变化 < 20% 且绝对 < 5 万 -> 不命中（坦克300 23.48 -> 25.00）。
  3. 绝对变化 >= 5 万但相对 < 20% -> 命中。
  4. 旧值是 None -> 不命中。
  5. 新值是 None（本次抓不到价格）-> 不命中。
  6. lo 和 hi 同时命中 -> reason 里两段都有。
  7. 按最大相对变化降序。
  8. 纯函数，不改传入对象。
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



def pr(model, lo, hi):
    return {"model": model, "price_lo": lo, "price_hi": hi}


def old(lo, hi):
    return {"lo": lo, "hi": hi, "asof": "2026-09-01"}


def main():
    # ---- 1. 奥德赛 ----
    existing = {"奥德赛": old(23.58, 43.98)}
    res = S.detect_price_jumps(existing, [pr("奥德赛", 18.48, 32.88)])
    check("奥德赛 上限 -25.2% 命中", len(res) == 1 and res[0]["model"] == "奥德赛")
    check("字段齐全",
          res[0]["old_lo"] == 23.58 and res[0]["old_hi"] == 43.98
          and res[0]["new_lo"] == 18.48 and res[0]["new_hi"] == 32.88)
    check("reason 含「上限 43.98 → 32.88（-25.2%）」", "上限 43.98 → 32.88（-25.2%）" in res[0]["reason"])
    check("键集合恰好是约定的 6 个", set(res[0]) == {"model", "old_lo", "old_hi", "new_lo", "new_hi", "reason"})

    up = S.detect_price_jumps({"星光730新能源": old(12.0, 15.0)}, [pr("星光730新能源", 12.0, 19.85)])
    check("上限 +32.3% 命中且 reason 带正号", len(up) == 1 and "（+32.3%）" in up[0]["reason"])

    # ---- 2. 不命中 ----
    res = S.detect_price_jumps({"坦克300": old(23.48, 23.48)}, [pr("坦克300", 25.00, 25.00)])
    check("坦克300 23.48 -> 25.00（+6.5%、+1.52 万）不命中", res == [])
    res = S.detect_price_jumps({"智己LS6": old(20.0, 30.0)}, [pr("智己LS6", 20.0, 26.25)])
    check("智己LS6 上限 -12.5%（绝对 3.75 万）不命中", res == [])
    res = S.detect_price_jumps({"不变": old(10.0, 20.0)}, [pr("不变", 10.0, 20.0)])
    check("价格没变不命中", res == [])
    res = S.detect_price_jumps({"新车型": old(10.0, 20.0)}, [pr("别的车型", 1.0, 2.0)])
    check("旧表里没有该车型（全新车型）不命中", res == [])
    res = S.detect_price_jumps({"车型": old(10.0, 20.0)}, [])
    check("本次没有任何价格行不命中", res == [])

    # ---- 3. 绝对阈值 ----
    res = S.detect_price_jumps({"豪车": old(100.0, 100.0)}, [pr("豪车", 100.0, 110.0)])
    check("hi 100 -> 110（+10%、+10 万）因绝对变化命中", len(res) == 1 and "上限 100.00 → 110.00（+10.0%）" in res[0]["reason"])
    check("只命中 hi 时 reason 不含「下限」", "下限" not in res[0]["reason"])

    # ---- 4/5. None ----
    res = S.detect_price_jumps({"无价": old(None, None)}, [pr("无价", 10.0, 50.0)])
    check("旧值是 None（原来无价格）不命中", res == [])
    res = S.detect_price_jumps({"半价": old(10.0, None)}, [pr("半价", 10.0, 50.0)])
    check("旧值只有一端为 None 也不命中", res == [])
    res = S.detect_price_jumps({"有价": old(10.0, 50.0)}, [pr("有价", None, None)])
    check("新值是 None（本次抓不到价格）不命中", res == [])
    res = S.detect_price_jumps({"有价": old(10.0, 50.0)}, [pr("有价", None, None), pr("有价", 10.0, 50.0)])
    check("同一车型多行时 None 行被忽略、取真实价格", res == [])
    res = S.detect_price_jumps({"有价": old(10.0, 50.0)}, [pr("有价", 10.0, 20.0), pr("有价", None, None)])
    check("真实价格之后又来一个 None 行，仍按真实价格判断", len(res) == 1)

    # ---- 6. lo 和 hi 同时命中 ----
    res = S.detect_price_jumps({"双命中": old(10.0, 20.0)}, [pr("双命中", 5.0, 30.0)])
    r = res[0]["reason"]
    check("lo/hi 同时命中 reason 两段都有", "下限 10.00 → 5.00（-50.0%）" in r and "上限 20.00 → 30.00（+50.0%）" in r)

    # ---- 7. 排序 ----
    existing = {"小": old(10.0, 20.0), "大": old(10.0, 20.0), "中": old(10.0, 20.0)}
    rows = [pr("小", 10.0, 24.4), pr("大", 10.0, 40.0), pr("中", 10.0, 28.0)]
    res = S.detect_price_jumps(existing, rows)
    check("按最大相对变化降序（大 100% > 中 40% > 小 22%）", [x["model"] for x in res] == ["大", "中", "小"])
    # 以 lo 的变化为准排序
    res = S.detect_price_jumps({"A": old(10.0, 20.0), "B": old(10.0, 20.0)}, [pr("A", 10.0, 25.0), pr("B", 4.0, 20.0)])
    check("最大相对变化取 lo/hi 中较大者（B 的 lo -60% 排在 A 的 hi +25% 前）", [x["model"] for x in res] == ["B", "A"])

    # 阈值参数可调
    res = S.detect_price_jumps({"坦克300": old(23.48, 23.48)}, [pr("坦克300", 25.00, 25.00)], lo_hi_threshold_pct=5.0)
    check("阈值参数可调（5% 时坦克300 命中）", len(res) == 1)

    # ---- 8. 纯函数 ----
    existing_in = {"奥德赛": old(23.58, 43.98), "坦克300": old(23.48, 23.48)}
    rows_in = [pr("奥德赛", 18.48, 32.88), pr("坦克300", 25.0, 25.0)]
    ex_copy, rows_copy = copy.deepcopy(existing_in), copy.deepcopy(rows_in)
    S.detect_price_jumps(existing_in, rows_in)
    check("不改传入的 existing_models", existing_in == ex_copy)
    check("不改传入的 price_rows", rows_in == rows_copy)

    # merge_price_updates 的返回签名保持不变
    merged = S.merge_price_updates({}, [pr("X", 1.0, 2.0)], "2026-10-01")
    check("merge_price_updates 仍返回 3 元组", isinstance(merged, tuple) and len(merged) == 3)


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
