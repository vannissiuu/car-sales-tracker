#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一份真实的品牌映射字典 (manufacturer_to_brand / model_to_brand) 替换进
sync.yml 里 DEFAULT_MAPPING_JSON 的占位内容（在 # ===== MAPPING_JSON_BEGIN/END =====
标记之间）。

用法:
    python3 inject_mapping.py <mapping.json路径> <sync.yml路径>

设计要点（这个脚本的健壮性比它做的事情本身更重要——它要处理的是别人产出的、
可能有 100+ 条目、大量中文、任意引号/反斜杠的真实字典，不能因为内容里出现某个
特殊字符就把 workflow 文件搞坏）：

  1. 先把输入文件当 JSON 解析一遍，解析失败直接报错退出，不碰目标文件。
  2. 用 json.dumps(..., ensure_ascii=False, indent=2) 重新序列化成规范格式——
     这一步顺带验证了"这份 JSON 能被正常读出来再写回去"，格式统一，也方便以后
     git diff 看得清楚。
  3. 嵌入 Python 源码的方式：
       - 默认用 r'''...''' 包起来（人眼可读）。
       - 只有当规范化后的 JSON 文本里真的出现了会破坏 r'''...''' 边界的 ''' 序列时，
         才自动降级成 base64 编码 + base64.b64decode(...).decode('utf-8') 的形式——
         这种形式不管内容是什么字符都不会把 Python 源码搞坏。多数情况下（包括真实
         的中文品牌字典）走的都是第一种，可读性更好；降级只在极端边界情况触发。
  4. 替换前备份原文件（带时间戳），替换只在内存里的新内容上先做完整验证：
       - yaml.safe_load 能解析
       - 定位到 "Write sync script" 步骤，提取内嵌 Python，compile() 通过
       - 把内嵌 Python 实际 exec 一遍（设置 __name__ 为非 "__main__"，main() 不会
         被触发），读出 DEFAULT_MAPPING_JSON，json.loads 解析，和输入的原始字典
         做值相等比较，确认"写进去的和读出来的是同一份数据"
     四步全部通过，才会真的覆盖 sync.yml；任何一步失败，原文件不改动，只留下
     已经生成的备份，报错信息里说明具体是哪一步失败。
  5. 只允许标记区块在文件里出现恰好一次——出现 0 次或多次都视为不安全，拒绝替换。
"""

import base64
import datetime
import json
import os
import re
import shutil
import sys

BEGIN_MARKER = "# ===== MAPPING_JSON_BEGIN ====="
END_MARKER = "# ===== MAPPING_JSON_END ====="


def fail(msg):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


def build_python_literal(normalized_json):
    """
    返回 (literal_source, mode_desc)。
    literal_source 是一段可以直接接在 "DEFAULT_MAPPING_JSON = " 后面的 Python 表达式源码。
    """
    # r'''...''' 在两种情况下不安全：
    #   a) 内容里出现了 ''' (三个连续单引号) —— 会提前把原始字符串截断
    #   b) 内容最后一个字符是单引号 —— 会和结尾的 ''' 连成 4 个引号，语法歧义/错误
    unsafe = ("'''" in normalized_json) or normalized_json.endswith("'")
    if not unsafe:
        literal = "r'''\n" + normalized_json + "\n'''"
        return literal, "raw triple-quoted string (r'''...''')"

    # 降级方案：base64，不管内容是什么字符都绝对安全。按 76 字符换行，避免生成
    # 一行几十/上百 KB 的超长源码行（有些工具对超长行不友好）。
    b64 = base64.b64encode(normalized_json.encode("utf-8")).decode("ascii")
    chunk_size = 76
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]
    quoted_chunks = "\n    ".join(f"'{c}'" for c in chunks)
    literal = (
        "base64.b64decode(\n    "
        + quoted_chunks
        + "\n).decode('utf-8')"
    )
    return literal, "base64 (内容含无法安全放进 r'''...''' 的引号序列，自动降级)"


def locate_marker_block(yml_text):
    pattern = re.compile(re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    matches = list(pattern.finditer(yml_text))
    if len(matches) == 0:
        fail(f"在目标文件里没有找到 {BEGIN_MARKER} ... {END_MARKER} 标记，拒绝替换")
    if len(matches) > 1:
        fail(f"找到 {len(matches)} 处标记区块，应该只有 1 处，为安全起见拒绝替换")
    m = matches[0]

    indent_match = re.search(r"^([ \t]*)" + re.escape(BEGIN_MARKER), yml_text, re.MULTILINE)
    indent = indent_match.group(1) if indent_match else ""
    return m.group(0), indent


def extract_embedded_python(yml_text):
    """
    传入原始 yml 文本，yaml.safe_load 之后定位 "Write sync script" 步骤，
    返回内嵌 Python 源码字符串 (已经去掉 heredoc 的首行 'cat > sync.py << ...' 和
    末尾的 'SYNC_SCRIPT_EOF' 标记)。定位不到就抛异常，调用方负责兜底。
    """
    import yaml

    doc = yaml.safe_load(yml_text)
    steps = doc["jobs"]["sync"]["steps"]
    write_step = next(s for s in steps if s.get("name") == "Write sync script")
    run_text = write_step["run"]
    lines = run_text.split("\n")
    if not lines or not lines[0].strip().startswith("cat > sync.py"):
        raise ValueError("'Write sync script' 步骤的第一行不是预期的 'cat > sync.py << ...'")
    body_lines = lines[1:]
    if body_lines and body_lines[-1].strip() == "":
        body_lines = body_lines[:-1]
    if body_lines and body_lines[-1].strip() == "SYNC_SCRIPT_EOF":
        body_lines = body_lines[:-1]
    else:
        raise ValueError("没有在 heredoc 末尾找到 SYNC_SCRIPT_EOF 标记")
    return "\n".join(body_lines)


def validate_yaml_and_compile(new_yml_text):
    """步骤1: yaml.safe_load 通过；步骤2: 内嵌 Python compile() 通过。失败返回 (False, msg)。"""
    try:
        code = extract_embedded_python(new_yml_text)
    except Exception as e:
        return False, f"yaml.safe_load / 定位内嵌 Python 失败: {e}", None
    try:
        compiled = compile(code, "sync.py", "exec")
    except SyntaxError as e:
        return False, f"内嵌 Python compile() 失败: {e}", None
    return True, "yaml.safe_load 通过；内嵌 Python compile() 通过", (code, compiled)


def validate_roundtrip(code, compiled, expected_data):
    """
    步骤3: 把内嵌 Python 实际 exec 一遍(不触发 main())，读出 DEFAULT_MAPPING_JSON，
    json.loads 解析，和期望的字典做值相等比较。
    """
    ns = {"__name__": "inject_mapping_roundtrip_check"}
    try:
        exec(compiled, ns)
    except Exception as e:
        return False, f"exec 内嵌 Python 失败: {type(e).__name__}: {e}"

    if "DEFAULT_MAPPING_JSON" not in ns:
        return False, "exec 后的命名空间里没有 DEFAULT_MAPPING_JSON 变量"

    try:
        roundtrip_data = json.loads(ns["DEFAULT_MAPPING_JSON"])
    except json.JSONDecodeError as e:
        return False, f"DEFAULT_MAPPING_JSON 不是合法 JSON: {e}"

    if roundtrip_data != expected_data:
        return False, "读回来的字典内容和输入不一致（值比较失败）"

    return True, (
        f"往返校验通过: 读回的字典与输入完全一致 "
        f"(manufacturer_to_brand {len(roundtrip_data.get('manufacturer_to_brand', {}))} 条, "
        f"model_to_brand {len(roundtrip_data.get('model_to_brand', {}))} 条)"
    )


def main():
    if len(sys.argv) != 3:
        print("用法: python3 inject_mapping.py <mapping.json路径> <sync.yml路径>", file=sys.stderr)
        sys.exit(2)

    mapping_path, yml_path = sys.argv[1], sys.argv[2]

    if not os.path.isfile(mapping_path):
        fail(f"找不到字典文件: {mapping_path}")
    if not os.path.isfile(yml_path):
        fail(f"找不到目标 workflow 文件: {yml_path}")

    with open(mapping_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        fail(f"{mapping_path} 不是合法 JSON: {e}")

    if not isinstance(data, dict):
        fail(f"{mapping_path} 顶层必须是一个 JSON 对象 (dict)，实际是 {type(data).__name__}")

    normalized_json = json.dumps(data, ensure_ascii=False, indent=2)
    literal_src, mode = build_python_literal(normalized_json)
    print(f"字典解析成功: manufacturer_to_brand {len(data.get('manufacturer_to_brand', {}))} 条, "
          f"model_to_brand {len(data.get('model_to_brand', {}))} 条")
    print(f"嵌入方式: {mode}")

    with open(yml_path, "r", encoding="utf-8") as f:
        yml_text = f.read()

    old_block, indent = locate_marker_block(yml_text)

    new_block = "\n".join([
        f"{indent}{BEGIN_MARKER}",
        f"{indent}# 本区块由 inject_mapping.py 自动生成，最近一次替换时间 (UTC): "
        f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"{indent}DEFAULT_MAPPING_JSON = {literal_src}",
        f"{indent}{END_MARKER}",
    ])

    new_yml_text = yml_text.replace(old_block, new_block, 1)
    if new_yml_text == yml_text:
        fail("替换没有产生任何变化（不应该发生，标记定位逻辑可能有问题）")

    # 备份原文件（在做任何真正的覆盖之前）
    backup_path = yml_path + ".bak." + datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    shutil.copy2(yml_path, backup_path)
    print(f"已备份原文件到 {backup_path}")

    # 校验 1+2: yaml 可解析 + 内嵌 Python 可 compile（在内存里的新内容上验证，不落盘）
    ok, msg, compiled_pair = validate_yaml_and_compile(new_yml_text)
    print(msg)
    if not ok:
        fail(f"替换后校验失败，原文件未改动: {msg}")

    # 校验 3: 往返一致性
    code, compiled = compiled_pair
    ok2, msg2 = validate_roundtrip(code, compiled, data)
    print(msg2)
    if not ok2:
        fail(f"替换后往返校验失败，原文件未改动: {msg2}")

    # 全部校验通过，才真正落盘覆盖目标文件
    with open(yml_path, "w", encoding="utf-8") as f:
        f.write(new_yml_text)

    print(f"替换成功: {yml_path}")


if __name__ == "__main__":
    main()
