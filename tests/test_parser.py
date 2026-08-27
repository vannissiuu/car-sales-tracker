#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用真实 HTML 样本离线验证 sync_script.py 的解析逻辑。
样本文件只读引用自 /tmp/cv3/probe_v3_output/，本脚本不修改它们。
"""

import os
import sys
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_script as S

SAMPLE_DIR = "/tmp/cv3/probe_v3_output"


def read_sample(name):
    path = os.path.join(SAMPLE_DIR, name)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def check(desc, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {desc}")
    if not cond:
        FAILURES.append(desc)


FAILURES = []


def main():
    print("=== 1. 主榜 2024-01 第1页 (01_style_202401_p1_raw.html) ===")
    html1 = read_sample("01_style_202401_p1_raw.html")
    rows1 = S.parse_style_like_table(html1)
    check("解析出 50 行", len(rows1) == 50)
    if rows1:
        r0 = rows1[0]
        print("  第1行:", r0)
        check("第1行车型是 长安CS75PLUS", r0["model"] == "长安CS75PLUS")
        check("第1行销量是 40496", r0["sales"] == 40496)
        check("第1行厂商是 长安汽车", r0["manufacturer"] == "长安汽车")

    total1 = S.extract_total_count(html1)
    print("  共N条 提取结果:", total1)
    check("总条数提取为 553", total1 == 553)

    print()
    print("=== 2. 主榜 2024-01 第2页 (02_style_202401_p2_raw.html) ===")
    html2 = read_sample("02_style_202401_p2_raw.html")
    rows2 = S.parse_style_like_table(html2)
    check("第2页解析出 50 行", len(rows2) == 50)
    if rows2:
        print("  第2页第1行:", rows2[0])
        check("第2页第1行排名是 51 (说明分页正确)", rows2[0]["rank"] == 51)

    print()
    print("=== 3. SUV 2024-01 (05_body5_suv_202401_p1_raw.html) ===")
    html5 = read_sample("05_body5_suv_202401_p1_raw.html")
    rows5 = S.parse_style_like_table(html5)
    check("SUV 页解析出 50 行", len(rows5) == 50)
    total5 = S.extract_total_count(html5)
    check("SUV 总条数提取为 291", total5 == 291)
    name5 = S.extract_body_type_name(html5)
    print("  提取到的车体类型中文名:", name5)
    check("车体类型中文名是 SUV", name5 == "SUV")

    print()
    print("=== 4. MPV 2024-01 (06_body3_mpv_202401_p1_raw.html) ===")
    html6 = read_sample("06_body3_mpv_202401_p1_raw.html")
    rows6 = S.parse_style_like_table(html6)
    check("MPV 页解析出 50 行", len(rows6) == 50)
    total6 = S.extract_total_count(html6)
    check("MPV 总条数提取为 52", total6 == 52)
    name6 = S.extract_body_type_name(html6)
    print("  提取到的车体类型中文名:", name6)
    check("车体类型中文名是 MPV", name6 == "MPV")

    print()
    print("=== 5. 新能源 2024-01 (07_ev_202401_p1_raw.html) ===")
    html7 = read_sample("07_ev_202401_p1_raw.html")
    rows7 = S.parse_style_like_table(html7)
    check("EV 页解析出 50 行", len(rows7) == 50)
    total7 = S.extract_total_count(html7)
    check("EV 总条数提取为 245", total7 == 245)
    if rows7:
        print("  EV 第1行:", rows7[0])

    print()
    print("=== 6. 厂商级 2024-01 (04_factory_202401_p1_raw.html) —— 列名不同，不应被当成 style 表解析 ===")
    html4 = read_sample("04_factory_202401_p1_raw.html")
    rows4 = S.parse_style_like_table(html4)
    check("厂商级页面列名('厂商LOGO'等)与预期不符，parse_style_like_table 应返回空列表", rows4 == [])
    total4 = S.extract_total_count(html4)
    check("厂商级页面总条数提取为 110", total4 == 110)

    print()
    print("=== 7. 编码探测：decode_response 对本地样本文件的等价检查 ===")
    # 样本文件是探针脚本已经解码好写盘的 utf-8 文本，这里只做一个 sanity check：
    # 确认解析出来的中文没有乱码（能在 rows 里看到正常汉字）。
    check("车型字段包含正常汉字（无乱码）", any("一" <= ch <= "鿿" for ch in rows1[0]["model"] + rows1[0]["manufacturer"]))

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
