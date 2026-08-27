#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单独验证 MAX_MONTHS 环境变量的解析逻辑（parse_max_months）。
覆盖 coordinator 指定的三种场景：'1' / '0' / ''（未设置）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

FAILURES = []


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


def main():
    print("=== 场景 1: MAX_MONTHS='1' (workflow_dispatch 传入默认值 '1') ===")
    val, desc = S.parse_max_months("1")
    print(f"  解析结果: max_months={val}, desc={desc!r}")
    check("MAX_MONTHS='1' -> max_months == 1 (只跑1个月)", val == 1)

    print()
    print("=== 场景 2: MAX_MONTHS='0' (用户手动选择不限制) ===")
    val, desc = S.parse_max_months("0")
    print(f"  解析结果: max_months={val}, desc={desc!r}")
    check("MAX_MONTHS='0' -> max_months == 0 (不限制，跑全部)", val == 0)

    print()
    print("=== 场景 3: MAX_MONTHS='' (未设置 / push 触发时 inputs 为空) ===")
    val, desc = S.parse_max_months("")
    print(f"  解析结果: max_months={val}, desc={desc!r}")
    check("MAX_MONTHS='' -> max_months == 1 (保守默认只跑1个月，不是跑全量)", val == 1)

    print()
    print("=== 场景 3b: MAX_MONTHS 环境变量整个不存在 (os.environ.get 返回 None) ===")
    os.environ.pop("MAX_MONTHS", None)
    val, desc = S.parse_max_months(os.environ.get("MAX_MONTHS"))
    print(f"  解析结果: max_months={val}, desc={desc!r}")
    check("环境变量完全不存在 (None) -> max_months == 1", val == 1)

    print()
    print("=== 场景 4 (边界): MAX_MONTHS='3' 用来验证 targets 截断逻辑本身 ===")
    val, desc = S.parse_max_months("3")
    all_targets = [(2024, 1), (2024, 2), (2024, 3), (2024, 4), (2024, 5)]
    targets = all_targets[:val] if val > 0 else all_targets
    print(f"  max_months={val}, 截断后的 targets={targets}")
    check("MAX_MONTHS='3' -> 截断后只剩前3个月", targets == [(2024, 1), (2024, 2), (2024, 3)])

    print()
    print("=== 场景 5 (边界): 非法输入 'abc' / 负数 '-1' 都应保守回退到 1 ===")
    val, _ = S.parse_max_months("abc")
    check("MAX_MONTHS='abc' (非法) -> max_months == 1", val == 1)
    val, _ = S.parse_max_months("-1")
    check("MAX_MONTHS='-1' (负数) -> max_months == 1", val == 1)

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
