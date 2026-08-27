#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 数据管道：从车主之家 xl.16888.com 抓取 2024-01 至今的乘用车月度销量数据，
产出 data/sales.csv / data/manufacturers.txt / data/sync_report.md。

设计依据见任务说明（已实测验证的事实、用户拍板口径），本文件不重复背景介绍。

关键设计点：
  - 幂等：启动时读取已有 data/sales.csv，跳过已经完整抓取过的 (year, month)。
  - 每个月只有在「主榜 + body-1..8 + ev」全部页面都抓取成功后才提交（append）到结果里；
    半途被拦截/超时的月份不写入，保证下次重跑不会因为"已存在该月份"而误跳过残缺数据。
  - 拦截检测（403/429/验证码关键词）：立即停止对该站点的所有后续请求，把已经跑完的
    完整月份数据保存下来，在报告里说明情况。
  - body_type 的分类名称（比如 body-5 到底叫"SUV"还是别的）是从页面 <title> 里现取的，
    不是硬编码猜的；body-1..8 里任何一个 404 / 没有结果就跳过该分类。
  - energy_type 严格二元：车型出现在 ev 榜单里 -> 新能源，否则 -> 燃油。不做任何按名字的推断。
"""

import base64
import csv
import io
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone

BASE = "https://xl.16888.com"

COMMON_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_HEADERS = {
    "User-Agent": COMMON_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

REQUEST_TIMEOUT_SECONDS = 20
SLEEP_BETWEEN_REQUESTS_SECONDS = 2
RETRY_SLEEPS = [5, 15]  # 失败重试 2 次，间隔 5s / 15s
ROWS_PER_PAGE = 50

BLOCK_MARKERS = [
    "验证码", "captcha", "人机验证", "安全验证", "访问异常",
    "拒绝访问", "禁止访问", "blocked", "Access Denied",
]

PAGINATION_PATTERNS = [
    r"共\s*(\d+)\s*条",
    r"共计\s*(\d+)\s*条",
]

# body-1 .. body-8 全部探测；含义未知的先占位，实际名称从页面标题里取
BODY_IDS = list(range(1, 9))

# 用户定死的最终口径：16888 原始的 5 个 body 分类 -> 用户要的 3+1 个类别。
# 两厢车/三厢车合并成轿车；MPV/SUV 原样；运动汽车数量太少(实测100行)，单独留一类
# 比塞进"其他"更诚实；不在这个表里的原始分类名（理论上不该出现，因为 body-6/7/8
# 实测不存在）原样透传，不静默吞掉，方便被人发现。
RAW_BODY_TYPE_TO_FINAL_CATEGORY = {
    "两厢车": "轿车",
    "三厢车": "轿车",
    "MPV": "MPV",
    "SUV": "SUV",
    "运动汽车": "运动汽车",
}

# 一个车型同时出现在多个 body 榜单、且映射到不同最终类别时的裁决优先级（从高到低）。
# SUV/MPV 是明确的车身形态，优先；两厢/三厢同款车合并后都是轿车，优先级对它们不产生
# 影响；运动汽车垫底。理由见 sync_script 使用处的说明。
CATEGORY_PRIORITY = ["SUV", "MPV", "轿车", "运动汽车"]

DATA_DIR = os.path.join(os.getcwd(), "data")
SALES_CSV = os.path.join(DATA_DIR, "sales.csv")
MANUFACTURERS_TXT = os.path.join(DATA_DIR, "manufacturers.txt")
REPORT_MD = os.path.join(DATA_DIR, "sync_report.md")
MAPPING_JSON_PATH = os.path.join(DATA_DIR, "mapping.json")

CSV_FIELDS = ["year", "month", "manufacturer", "brand", "model", "body_type", "energy_type", "sales"]

# ===== MAPPING_JSON_BEGIN =====
# 厂商/车型 -> 品牌 映射字典（定稿版，117 家厂商全覆盖 + 87 条车型级覆盖，见下方 _meta）。
# 维护在仓库的 data/mapping.json 里，用户/coordinator 可以直接在 GitHub 网页上编辑它，
# 不需要重新粘贴整个 workflow。
# 这里的内容只是"仓库里还没有 data/mapping.json 时"的一次性自举默认值——
# 脚本发现文件不存在才会把这份内容写出去，文件已存在时绝不覆盖（哪怕内容和这里不一样）。
# 这一段可以用 /tmp/p1-sync/inject_mapping.py 自动生成/替换，人工编辑也可以，
# 但要保证 DEFAULT_MAPPING_JSON 最终是一个合法 JSON 字符串。
DEFAULT_MAPPING_JSON = r'''
{
  "_meta": {
    "description": "厂商/车型 → 品牌 映射字典，用于中国汽车销量看板的『厂商/品牌』视角切换",
    "version": "1.0",
    "resolution_order": "model_to_brand 优先（按车型名精确匹配），其次 manufacturer_to_brand，都没有则回退为 manufacturer 原值（本数据集18814行中，回退发生次数应为0，见 self-check）",
    "generated_from": "data/manufacturers.txt (117 家), data/sales.csv (18814 行, 2024-01 至 2026-07 共31个月, 累计57,124,329辆)",
    "rules": "1. 品牌 = 消费者认知里的那个车标/emblem，不是母公司或合资公司名。如 上汽大众/一汽-大众 → 大众；上汽大众斯柯达 → 斯柯达；一汽-大众捷达 → 捷达（2019年已独立成品牌）。2. 同一合资公司下的多个独立品牌要拆开：上汽通用别克/雪佛兰/凯迪拉克 → 三个独立品牌；长安福特/长安马自达/长安林肯 → 福特/马自达/林肯。3. 自主品牌集团下已独立运营的子品牌要拆开：长安启源→启源、广汽埃安→埃安；同一车标的不同生产主体要合并：五菱新能源 和 上汽通用五菱 都→五菱；一汽海马 和 海马汽车 都→海马。4. 合资公司的外方品牌名统一为中文通行译名：一汽奥迪/上汽奥迪→奥迪，广汽本田/东风本田→本田。5. 比亚迪子品牌方程豹/腾势/仰望：经用户拍板，按独立品牌处理，不并入比亚迪（决定2）。6. 华为鸿蒙智行『界』系列（问界/智界/享界/尊界/尚界）：厂商名与品牌名基本一一对应，直接采用（问界/智界经确认采纳）。7. 【核心规则，决定1】当一个厂商条目内混合多个消费者认知品牌（不同车标/渠道/定位）时，manufacturer_to_brand 只给一个保守的『哨兵/兜底』值（通常等于厂商原名，蔚来除外——蔚来主品牌本身也叫『蔚来』故兜底值恰好正确），真正的品牌归类通过 model_to_brand 按车型名精确匹配实现，解析时 model_to_brand 优先级高于 manufacturer_to_brand。适用：长城汽车（哈弗/坦克/魏牌）、蔚来（蔚来/乐道/萤火虫）、上汽集团（荣威/MG/飞凡/科莱威）、奇瑞捷豹路虎（捷豹/路虎）；此外星途（星途/星纪元）、北京汽车制造厂（锐胜/勇士/元宝/家宝/212）、江汽集团（江淮/瑞风/钇为/爱跑/花仙子）、江铃集团新能源（易至/羿）也按同一机制处理，只是这些厂商没有强制要求做到100%精细拆分，manufacturer_to_brand的默认值已覆盖大部分销量。8. 兜底值选择原则：优先选'哪怕未来出现字典未覆盖的新车型，这个值也大概率仍然正确'的选项——蔚来主品牌用『蔚来』；无法判断哪个子品牌最具代表性、或用主品牌名会造成后续新车型被误分类风险的（长城汽车/上汽集团/奇瑞捷豹路虎），选择厂商原名作为哨兵值，使其在品牌视角下清晰地表现为'未分类，需要更新model_to_brand'，而不是悄悄地被错误合并进某个具体子品牌。9. 拿不准、样本量小、无法用现有知识确认的车型/厂商，一律记录进 _unresolved_notes 并给出已采用的近似处理方式，不藏在字典里假装确定。"
  },
  "manufacturer_to_brand": {
    "DS汽车": "DS",
    "LEVC": "LEVC",
    "MAEXTRO 尊界": "尊界",
    "Polestar": "极星",
    "ROX极石": "极石",
    "SAIC 尚界": "尚界",
    "STELATO 享界": "享界",
    "SWM斯威汽车": "斯威",
    "smart": "smart",
    "一汽-大众": "大众",
    "一汽-大众捷达": "捷达",
    "一汽丰田": "丰田",
    "一汽吉林": "森雅",
    "一汽奔腾": "奔腾",
    "一汽奥迪": "奥迪",
    "一汽海马": "海马",
    "一汽红旗": "红旗",
    "上汽大众": "大众",
    "上汽大众斯柯达": "斯柯达",
    "上汽大通": "大通",
    "上汽奥迪": "奥迪",
    "上汽通用五菱": "五菱",
    "上汽通用凯迪拉克": "凯迪拉克",
    "上汽通用别克": "别克",
    "上汽通用雪佛兰": "雪佛兰",
    "东风乘用车": "风神",
    "东风小康": "风光",
    "东风日产": "日产",
    "东风本田": "本田",
    "东风标致": "标致",
    "东风汽车": "纳米",
    "东风英菲尼迪": "英菲尼迪",
    "东风雪铁龙": "雪铁龙",
    "东风风行": "风行",
    "中国重汽VGV": "VGV",
    "二一二越野车": "212",
    "五菱新能源": "五菱",
    "凯翼汽车": "凯翼",
    "创维汽车": "创维",
    "北京奔驰": "奔驰",
    "北京汽车": "北京",
    "北京现代": "现代",
    "北京越野": "北京越野",
    "北汽新能源": "极狐",
    "华人运通": "高合",
    "华晨宝马": "宝马",
    "合众汽车": "哪吒",
    "合创汽车": "合创",
    "吉利几何": "几何",
    "吉利新能源": "吉利",
    "吉利汽车": "吉利",
    "吉麦新能源": "吉麦",
    "大运汽车": "大运",
    "奇瑞新能源": "奇瑞",
    "奇瑞汽车": "奇瑞",
    "奕派科技": "奕派",
    "小米汽车": "小米",
    "小虎汽车": "小虎",
    "小鹏汽车": "小鹏",
    "岚图汽车": "岚图",
    "广汽丰田": "丰田",
    "广汽乘用车": "传祺",
    "广汽埃安": "埃安",
    "广汽本田": "本田",
    "广汽本田新能源": "本田",
    "开瑞汽车": "开瑞",
    "悦达起亚": "起亚",
    "昊铂": "昊铂",
    "智己汽车": "智己",
    "曹操汽车": "曹操",
    "极氪": "极氪",
    "极越汽车": "极越",
    "比亚迪": "比亚迪",
    "江铃福特": "福特",
    "沃尔沃亚太": "沃尔沃",
    "海马汽车": "海马",
    "深蓝汽车": "深蓝",
    "特斯拉中国": "特斯拉",
    "猛士科技": "猛士",
    "理想": "理想",
    "瑞驰新能源": "瑞驰",
    "睿蓝汽车": "睿蓝",
    "知豆电动车": "知豆",
    "神龙汽车": "雪铁龙",
    "福建奔驰": "奔驰",
    "福汽新龙马": "启腾",
    "赛力斯蓝电": "蓝电",
    "远航汽车": "远航",
    "郑州日产": "日产",
    "金康赛力斯": "赛力斯",
    "鑫源汽车": "鑫源",
    "长城新能源": "欧拉",
    "长安凯程": "凯程",
    "长安启源": "启源",
    "长安林肯": "林肯",
    "长安汽车": "长安",
    "长安福特": "福特",
    "长安马自达": "马自达",
    "阿维塔科技": "阿维塔",
    "零跑汽车": "零跑",
    "领克": "领克",
    "领途汽车": "领途",
    "方程豹": "方程豹",
    "腾势汽车": "腾势",
    "仰望": "仰望",
    "AITO 问界": "问界",
    "LUXCEED 智界": "智界",
    "大众汽车安徽": "与众",
    "光束汽车": "MINI",
    "江汽集团": "江淮",
    "江铃集团新能源": "易至",
    "北京汽车制造厂": "锐胜",
    "星途": "星途",
    "长城汽车": "长城汽车",
    "蔚来": "蔚来",
    "上汽集团": "上汽集团",
    "奇瑞捷豹路虎": "奇瑞捷豹路虎"
  },
  "model_to_brand": {
    "哈弗大狗": "哈弗",
    "哈弗H6": "哈弗",
    "哈弗猛龙新能源": "哈弗",
    "哈弗枭龙MAX": "哈弗",
    "哈弗猛龙": "哈弗",
    "哈弗H9": "哈弗",
    "哈弗H5": "哈弗",
    "哈弗M6": "哈弗",
    "哈弗大狗 PLUS 新能源": "哈弗",
    "哈弗赤兔": "哈弗",
    "哈弗神兽": "哈弗",
    "哈弗H6新能源": "哈弗",
    "哈弗酷狗": "哈弗",
    "坦克300": "坦克",
    "坦克500新能源": "坦克",
    "坦克400新能源": "坦克",
    "坦克300新能源": "坦克",
    "坦克700新能源": "坦克",
    "坦克400": "坦克",
    "坦克500": "坦克",
    "魏牌 高山": "魏牌",
    "魏牌 蓝山": "魏牌",
    "魏牌 V9X": "魏牌",
    "魏牌 摩卡新能源": "魏牌",
    "魏牌 拿铁DHT-PHEV": "魏牌",
    "蔚来ES8": "蔚来",
    "蔚来ES6": "蔚来",
    "蔚来ET5T": "蔚来",
    "蔚来EC6": "蔚来",
    "蔚来ET5": "蔚来",
    "蔚来ES9": "蔚来",
    "蔚来ET7": "蔚来",
    "蔚来EC7": "蔚来",
    "蔚来ET9": "蔚来",
    "蔚来ES7": "蔚来",
    "乐道L60": "乐道",
    "乐道L90": "乐道",
    "乐道L80": "乐道",
    "firefly萤火虫": "萤火虫",
    "荣威i5": "荣威",
    "荣威D7": "荣威",
    "荣威RX5": "荣威",
    "荣威D6": "荣威",
    "荣威i6 MAX新能源": "荣威",
    "荣威M7 DMH": "荣威",
    "荣威D5X DMH": "荣威",
    "荣威iMAX8新能源": "荣威",
    "荣威RX5新能源": "荣威",
    "荣威i6": "荣威",
    "荣威Ei5": "荣威",
    "荣威iMAX8": "荣威",
    "荣威RX9": "荣威",
    "MG4": "MG",
    "MG5": "MG",
    "MG7": "MG",
    "MG 4X": "MG",
    "MG ES5": "MG",
    "MG ONE": "MG",
    "MG6": "MG",
    "MG Cyberster": "MG",
    "名爵ZS": "MG",
    "飞凡F7": "飞凡",
    "飞凡R7": "飞凡",
    "科莱威CLEVER": "科莱威",
    "捷豹XFL": "捷豹",
    "捷豹XEL": "捷豹",
    "捷豹E-PACE": "捷豹",
    "揽胜极光": "路虎",
    "发现运动": "路虎",
    "发现运动版新能源": "路虎",
    "揽胜极光新能源": "路虎",
    "星纪元 ET": "星纪元",
    "勇士": "勇士",
    "元宝": "元宝",
    "家宝": "家宝",
    "212经典": "212",
    "瑞风M3": "瑞风",
    "瑞风E3": "瑞风",
    "瑞风RF8 PHEV": "瑞风",
    "瑞风RF8": "瑞风",
    "钇为3": "钇为",
    "爱跑": "爱跑",
    "花仙子": "花仙子",
    "羿": "羿",
    "羿驰05": "羿",
    "羿驰01": "羿",
    "羿驰05S": "羿"
  },
  "_unresolved_notes": [
    {
      "item": "北京汽车制造厂 / 北汽L6H",
      "note": "销量很小（122辆，占该厂商总量的0.27%）。命名规律与'锐胜王牌M7/M8'系列不同，无法确认它是锐胜品牌下的另一款卡车，还是北汽制造厂旗下未识别的其他子品牌。未列入 model_to_brand，会按 manufacturer_to_brand 默认值回退为'锐胜'——这是一个未经证实的猜测，不是确定归类，请知悉。"
    },
    {
      "item": "东风汽车 / 示界06",
      "note": "销量较小（2,053辆，占该厂商总量的1.72%）。厂商'东风汽车'旗下车型以'纳米'品牌(纳米01/06/BOX)为主，但'示界06'命名方式不同，无法确认它是否仍属于纳米品牌，还是东风旗下另一个未识别的新子品牌。未列入 model_to_brand，会按manufacturer_to_brand 默认值回退为'纳米'——这是一个未经证实的猜测，请知悉。"
    },
    {
      "item": "上汽集团 / 科莱威CLEVER",
      "note": "'科莱威CLEVER'（838辆）已映射为独立品牌'科莱威'。这是上汽旗下一个知名度较低的微型电动车品牌，我对它的品牌独立性有一定把握，但不如荣威/MG/飞凡确定，供留意。"
    },
    {
      "item": "江汽集团 / 爱跑、花仙子",
      "note": "'爱跑'（5,012辆）和'花仙子'（4,076辆）已各自映射为同名独立品牌。这两个是江淮旗下知名度较低的微型电动车产品线，我对它们是否应算'独立品牌'还是应并入'江淮'或'钇为'把握不是特别足（体量小，江淮系微型车的品牌层级本身对外宣传也不算清晰），供确认。"
    },
    {
      "item": "广汽本田新能源 / 绎乐",
      "note": "销量极小（2辆）。'绎乐'可能属于本田在华新推出的电动车子品牌'烨'(Ye)，而不是传统本田品牌，但因样本量为2辆、且我对'烨'品牌在这份数据里的具体命名规则把握不足，暂时保留映射为'本田'（原有规则：广汽本田新能源→本田），未额外拆分，供留意，影响可忽略。"
    },
    {
      "item": "大众汽车安徽 / 与众全系（与众06/07/08）",
      "note": "已按上次建议定为独立品牌'与众'（不并入'大众'）。销量很小（2,149辆，占比0.004%），但我对'与众'这个新品牌在消费者认知里独立于大众VW车标的程度依然把握不是100%，供留意，实际影响可忽略。"
    }
  ],
  "uncertain": []
}
'''
# ===== MAPPING_JSON_END =====

# 单次 job 的软性时间预算（留出余量给 commit / upload 步骤），超过后停止抓新的月份
MAX_RUNTIME_SECONDS = 5.5 * 3600


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class BlockedException(Exception):
    """站点判定为拦截（403/429/验证码关键词），调用方需立即停止一切后续请求。"""


class MonthAbortedException(Exception):
    """当前月份抓取半途失败（非拦截，比如反复重试仍失败的页面），放弃这一个月，不写入结果。"""


# ---------------------------------------------------------------------------
# 编码处理
# ---------------------------------------------------------------------------

def decode_response(resp):
    raw = resp.content
    meta_charset = None
    try:
        m = re.search(rb'charset\s*=\s*["\']?\s*([a-zA-Z0-9_-]+)', raw[:4096])
        if m:
            meta_charset = m.group(1).decode("ascii", errors="ignore").strip().lower().strip('"\'/')
    except Exception:
        pass

    candidates = []
    if meta_charset:
        candidates.append(meta_charset)
    candidates.append("gb18030")
    try:
        if resp.apparent_encoding:
            candidates.append(resp.apparent_encoding)
    except Exception:
        pass
    candidates.append("utf-8")

    for enc in candidates:
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 解析：条数 / 表格 / 分类中文名
# ---------------------------------------------------------------------------

def extract_total_count(html_text):
    """从页面文字里提取「共XXX条」的 XXX。找不到返回 None。"""
    for pat in PAGINATION_PATTERNS:
        m = re.search(pat, html_text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_body_type_name(html_text):
    """
    从 <title> 里提取车体类型的中文名称，比如：
      "2024年1月SUV销量排行榜，SUV汽车销量查询..." -> "SUV"
      "2024年1月MPV销量排行榜，..."                -> "MPV"
    取不到返回 None（调用方应跳过，不要瞎猜）。
    """
    m = re.search(r"<title>(.*?)</title>", html_text, re.S)
    if not m:
        return None
    title = m.group(1).strip()
    m2 = re.search(r"\d+年\d+月(.+?)销量排行榜", title)
    if m2:
        name = m2.group(1).strip()
        if name:
            return name
    return None


def parse_style_like_table(html_text):
    """
    解析「主榜 / body-N / ev」这类页面的表格。
    列名固定是 ['排名', '车型', '销量', '厂商', '售价（万元）', '车型相关']。
    返回 list[dict]，每个 dict 至少有 model / manufacturer / sales / rank 四个键。
    解析不到表格、或者列名对不上，返回空列表（调用方按"这页没数据"处理）。
    """
    try:
        import pandas as pd
    except Exception as e:
        log(f"  !! import pandas 失败: {e}")
        return []

    try:
        dfs = pd.read_html(io.StringIO(html_text))
    except Exception as e:
        log(f"  !! pd.read_html 失败: {type(e).__name__}: {e}")
        return []

    if not dfs:
        return []

    df = dfs[0]
    df.columns = [str(c) for c in df.columns]

    expected = {"排名", "车型", "销量", "厂商"}
    if not expected.issubset(set(df.columns)):
        log(f"  !! 表格列名不符合预期，实际列名: {list(df.columns)}")
        return []

    rows = []
    for _, r in df.iterrows():
        try:
            model = str(r["车型"]).strip()
            manufacturer = str(r["厂商"]).strip()
            sales = int(r["销量"])
            rank = int(r["排名"])
        except (ValueError, TypeError, KeyError):
            continue
        if not model or model.lower() == "nan":
            continue
        rows.append({
            "rank": rank,
            "model": model,
            "manufacturer": manufacturer,
            "sales": sales,
        })
    return rows


# ---------------------------------------------------------------------------
# 网络：抓取单页 / 抓取一个榜单的全部页
# ---------------------------------------------------------------------------

def fetch_page(session, url, allow_404_as_empty=False):
    """
    抓取单个页面，内置失败重试（2 次，5s/15s 间隔）。
    返回 (html_text, status, http_code)：
      - status 为 None 表示成功；'empty' 表示 allow_404_as_empty=True 时遇到 404
        （用于探测 body-N 是否存在）；其他字符串是失败原因描述。
      - http_code 是最后一次实际拿到的 HTTP 状态码（int），请求异常（连不上/超时）时为 None。
        这个值主要是给报告用的，不影响控制流。
      - 命中 403/429/验证码关键词会直接抛出 BlockedException，不走正常返回路径。
    """
    import requests

    last_err = None
    last_http_code = None
    attempts = 1 + len(RETRY_SLEEPS)
    for attempt in range(attempts):
        try:
            resp = session.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        except Exception as e:
            last_err = f"请求异常: {e}"
            last_http_code = None
            if attempt < attempts - 1:
                time.sleep(RETRY_SLEEPS[attempt])
                continue
            return None, last_err, last_http_code

        last_http_code = resp.status_code

        if resp.status_code in (403, 429):
            raise BlockedException(f"HTTP {resp.status_code} on {url}")

        if resp.status_code == 404:
            if allow_404_as_empty:
                return None, "empty", last_http_code
            last_err = "HTTP 404"
            if attempt < attempts - 1:
                time.sleep(RETRY_SLEEPS[attempt])
                continue
            return None, last_err, last_http_code

        if resp.status_code != 200:
            last_err = f"HTTP {resp.status_code}"
            if attempt < attempts - 1:
                time.sleep(RETRY_SLEEPS[attempt])
                continue
            return None, last_err, last_http_code

        text = decode_response(resp)
        if any(marker.lower() in text.lower() for marker in BLOCK_MARKERS):
            raise BlockedException(f"命中拦截关键词 on {url}")

        time.sleep(SLEEP_BETWEEN_REQUESTS_SECONDS)
        return text, None, last_http_code

    return None, last_err or "重试耗尽", last_http_code


def fetch_full_listing(session, url_template, report, label, allow_missing=False):
    """
    抓取一个榜单（主榜 / 某个 body-N / ev）在某个月份的全部页面，拼成一个 rows 列表。
    url_template 是形如 "https://xl.16888.com/style-202401-202401-{page}.html" 的字符串。

    返回 (rows, ok, page1_html, probe)：
      - allow_missing=True 且第 1 页 404：视为该分类当月不存在，返回 ([], True, None, probe) —— 不算失败，直接跳过。
      - 抓取过程中反复失败：返回 (None, False, None, probe)，调用方应放弃整个月份。
      - page1_html 是第 1 页的原始文本，调用方可以用它顺便提取分类中文名，不用再多发一次请求。
      - probe 是一个 dict：{"http_status": int或None, "total": int或None, "parsed_rows": int或None,
        "error": str或None}，专门给 body-1..8 探测报告用，不影响任何控制流。
    """
    probe = {"http_status": None, "total": None, "parsed_rows": None, "error": None}

    page1_url = url_template.format(page=1)
    html, status, http_code = fetch_page(session, page1_url, allow_404_as_empty=allow_missing)
    page1_html = html
    probe["http_status"] = http_code

    if status == "empty":
        log(f"  [{label}] 第1页 404，视为该分类不存在，跳过")
        probe["error"] = "第1页 404（该分类当月不存在）"
        return [], True, None, probe

    if html is None:
        report["failures"].append(f"{label} 第1页失败: {status} ({page1_url})")
        probe["error"] = status
        return None, False, None, probe

    total = extract_total_count(html)
    probe["total"] = total
    rows = parse_style_like_table(html)
    if not rows and total is None:
        # 页面能打开但既没有表格也没有条数提示，当作"这个分类当月没有数据"
        log(f"  [{label}] 第1页无表格无条数提示，视为空")
        probe["error"] = "第1页无表格无条数提示"
        probe["parsed_rows"] = 0
        return [], True, page1_html, probe

    if total is not None:
        total_pages = (total + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE
    else:
        # 没抓到"共N条"，退化成"抓到空页就停"，加一个安全上限防止死循环
        total_pages = 40

    all_rows = list(rows)
    page = 2
    while page <= total_pages:
        url = url_template.format(page=page)
        html, status, _http_code = fetch_page(session, url)
        if html is None:
            report["failures"].append(f"{label} 第{page}页失败: {status} ({url})")
            probe["error"] = f"第{page}页失败: {status}"
            probe["parsed_rows"] = len(all_rows)
            return None, False, None, probe
        page_rows = parse_style_like_table(html)
        if not page_rows:
            if total is None:
                break  # 退化模式下，空页即结束
            # 有 total 却解析不到行，记为失败页但不中断整月（数据仍不完整，谨慎起见整月放弃）
            report["failures"].append(f"{label} 第{page}页解析为空但预期有数据 ({url})")
            probe["error"] = f"第{page}页解析为空但预期有数据"
            probe["parsed_rows"] = len(all_rows)
            return None, False, None, probe
        all_rows.extend(page_rows)
        page += 1

    if total is not None and len(all_rows) != total:
        log(f"  [{label}] 警告: 抓到 {len(all_rows)} 行，页面声明共 {total} 条")
        probe["error"] = f"抓到{len(all_rows)}行 != 声明{total}条"

    probe["parsed_rows"] = len(all_rows)
    return all_rows, True, page1_html, probe


# ---------------------------------------------------------------------------
# 单月同步
# ---------------------------------------------------------------------------

def _record_body_probe(report, year, month, body_id, http_status, name, total, parsed_rows, error):
    """记录一条 body 探测结果，返回这条记录本身（调用方可以在本月内复用，不用重新拼一遍）。"""
    entry = {
        "year": year, "month": month, "body_id": body_id,
        "http_status": http_status, "name": name,
        "total": total, "parsed_rows": parsed_rows, "error": error,
    }
    report.setdefault("body_probe", []).append(entry)
    return entry


def _record_cross_category_models(report, year, month, cross_category_models):
    """
    把本月 build_body_type_map 发现的"出现在 >=2 个 body 榜单里的车型"合并进全局汇总
    （按车型名去重——同一款车每个月都会出现在同样的榜单组合里，没必要按月重复列，
    报告里改成显示"出现过的月份数"）。这是给人看的诊断信息，不是告警。
    """
    global_map = report.setdefault("cross_category_models", {})
    for item in cross_category_models:
        model = item["model"]
        entry = global_map.setdefault(model, {
            "model": model, "occurrences": item["occurrences"],
            "final_category": item["final_category"], "months_seen": [],
        })
        entry["occurrences"] = item["occurrences"]
        entry["final_category"] = item["final_category"]
        entry["months_seen"].append(f"{year}-{month:02d}")


def sync_month(session, year, month, report, body_names_cache, mapping):
    yyyymm = f"{year:04d}{month:02d}"
    log(f"开始抓取 {year}-{month:02d}")

    style_tpl = f"{BASE}/style-{yyyymm}-{yyyymm}-{{page}}.html"
    style_rows, ok, _page1_html, _probe = fetch_full_listing(session, style_tpl, report, f"{yyyymm} 主榜")
    if not ok or not style_rows:
        raise MonthAbortedException(f"{yyyymm} 主榜抓取失败或为空")

    body_lists = []  # [(body_id, body_name, rows), ...] —— 仅本月成功探测到、有数据的分类
    this_month_probe = []  # 本月所有 body-1..8 的探测记录（含失败/不存在的），供对账用

    for body_id in BODY_IDS:
        body_tpl = f"{BASE}/body-{body_id}-{yyyymm}-{yyyymm}-{{page}}.html"
        label = f"{yyyymm} body-{body_id}"
        try:
            rows, ok, page1_html, probe = fetch_full_listing(
                session, body_tpl, report, label, allow_missing=True
            )
        except BlockedException:
            # 探测报告里也要留一行，说明这个 body 编号是因为被拦截才没探测到，
            # 而不是"这个分类不存在"——这两者语义完全不同，不能让报告误导用户。
            _record_body_probe(
                report, year, month, body_id,
                http_status=None, name=None, total=None, parsed_rows=None,
                error="触发站点拦截 (403/429/验证码)，抓取被中止",
            )
            raise

        if not ok:
            _record_body_probe(
                report, year, month, body_id,
                http_status=probe.get("http_status"), name=None,
                total=probe.get("total"), parsed_rows=probe.get("parsed_rows"),
                error=probe.get("error") or "抓取失败",
            )
            raise MonthAbortedException(f"{label} 抓取失败")

        if not rows:
            entry = _record_body_probe(
                report, year, month, body_id,
                http_status=probe.get("http_status"), name=None,
                total=probe.get("total"), parsed_rows=probe.get("parsed_rows") or 0,
                error=probe.get("error") or "无数据",
            )
            this_month_probe.append(entry)
            continue

        if body_id not in body_names_cache:
            name = extract_body_type_name(page1_html) if page1_html else None
            body_names_cache[body_id] = name or f"未知分类{body_id}"
        body_name = body_names_cache[body_id]

        entry = _record_body_probe(
            report, year, month, body_id,
            http_status=probe.get("http_status"), name=body_name,
            total=probe.get("total"), parsed_rows=probe.get("parsed_rows"),
            error=probe.get("error"),
        )
        this_month_probe.append(entry)
        body_lists.append((body_id, body_name, rows))

    body_type_map, conflicts, cross_category_models = build_body_type_map(body_lists)
    if conflicts:
        report.setdefault("body_type_conflicts", []).extend(f"{yyyymm}: {c}" for c in conflicts)
    _record_cross_category_models(report, year, month, cross_category_models)

    ev_tpl = f"{BASE}/ev-{yyyymm}-{yyyymm}-{{page}}.html"
    ev_rows, ok, _page1_html, _probe = fetch_full_listing(
        session, ev_tpl, report, f"{yyyymm} ev", allow_missing=True
    )
    if not ok:
        raise MonthAbortedException(f"{yyyymm} ev 抓取失败")
    ev_set = build_ev_set(ev_rows or [])

    out_rows, unmatched = label_rows(style_rows, body_type_map, ev_set, mapping, year, month)

    recon = reconcile_categories(out_rows, body_lists)
    report.setdefault("reconciliation", []).append({
        "year": year, "month": month,
        "results": recon["results"],
        "total_check": recon["total_check"],
    })

    log(f"完成 {yyyymm}: 主榜 {len(style_rows)} 行, 填「其他」{unmatched} 行, "
        f"新能源 {sum(1 for x in out_rows if x['energy_type']=='新能源')} 行, "
        f"对账{'通过' if recon['total_check']['ok'] else '不平'}")

    report["months_done"].append({
        "year": year, "month": month,
        "rows": len(out_rows), "unmatched_body_type": unmatched,
    })
    return out_rows


def map_to_final_category(raw_body_name):
    """原始 body 分类名 -> 用户口径的最终类别。未知原始名原样透传，不静默吞掉。"""
    return RAW_BODY_TYPE_TO_FINAL_CATEGORY.get(raw_body_name, raw_body_name)


def resolve_category_priority(final_categories_seen):
    """
    一个车型同时落在多个最终类别里时（比如既在SUV榜又在三厢车榜），按
    CATEGORY_PRIORITY 裁决用哪个。理论上不该出现 CATEGORY_PRIORITY 之外的类别
    （已知 5 个原始分类全部覆盖），万一将来数据源新增了没见过的类别，不要抛异常，
    按字典序兜底选一个，保证流程不中断，异常情况会在跨分类车型报告里被人看到。
    """
    for cat in CATEGORY_PRIORITY:
        if cat in final_categories_seen:
            return cat
    if final_categories_seen:
        return sorted(final_categories_seen)[0]
    return "其他"


def build_body_type_map(body_lists):
    """
    body_lists: [(body_id, raw_body_name, rows), ...]，rows 里每个元素至少有 'model' 键。
    关联键只用车型名（同一个月内车型名 100% 唯一，已由用户核实过，不再带厂商名一起比较）。

    先把每个车型在各个 body 榜单里出现过的原始分类名（映射成最终类别后）全部收集起来，
    再按 CATEGORY_PRIORITY 裁决出唯一的最终类别——不再依赖"后处理的 body 覆盖先处理的
    body"这种隐式的、依赖 BODY_IDS 遍历顺序的写法。

    返回 (body_type_map: model -> 最终类别, conflicts: list[str], cross_category_models: list[dict])：
      - conflicts：同一个 body 榜单内同一个车型出现多次（真正的数据异常，不是两厢/三厢
        同款这种正常情况）。
      - cross_category_models：出现在 >=2 个不同 body 榜单里的车型，不管裁决后是否
        同一个最终类别（两厢+三厢合并成轿车的车，比如朗逸，也会出现在这里，这是预期的、
        诚实的记录，不是异常告警——真正需要人工关注的是它旁边列出的"最终类别"是不是
        SUV/MPV 这类优先级生效的情况）。
    这是一个纯函数，不发请求，方便离线单测。
    """
    occurrences = {}  # model -> [(body_id, raw_body_name), ...]
    conflicts = []
    for body_id, raw_body_name, rows in body_lists:
        counts_in_this_body = {}
        for r in rows:
            counts_in_this_body[r["model"]] = counts_in_this_body.get(r["model"], 0) + 1

        for model, c in counts_in_this_body.items():
            if c > 1:
                conflicts.append(f"body-{body_id}({raw_body_name}) 榜单内车型「{model}」重复出现 {c} 次")

        for model in counts_in_this_body:
            occurrences.setdefault(model, []).append((body_id, raw_body_name))

    body_type_map = {}
    cross_category_models = []
    for model, occ_list in occurrences.items():
        final_categories_seen = {map_to_final_category(raw_name) for _bid, raw_name in occ_list}
        final_category = resolve_category_priority(final_categories_seen)
        body_type_map[model] = final_category
        if len(occ_list) >= 2:
            cross_category_models.append({
                "model": model,
                "occurrences": list(occ_list),
                "final_category": final_category,
            })

    return body_type_map, conflicts, cross_category_models


def build_ev_set(ev_rows):
    """ev 榜单车型名集合（仅按车型名，理由同 build_body_type_map）。纯函数，方便单测。"""
    return {r["model"] for r in ev_rows}


def resolve_brand_with_source(model, manufacturer, mapping):
    """
    品牌解析优先级（和另一个代理产出的字典契约一致）：
      1. model_to_brand（按车型名）——长城/蔚来/上汽集团/奇瑞捷豹路虎这类一个厂商装
         多个品牌的情况，只有按车型才能分清楚。
      2. manufacturer_to_brand（按厂商名）。
      3. 都没命中 -> 直接用 manufacturer 原值兜底，绝不返回空字符串。
    返回 (brand, source)，source 是 "model" / "manufacturer" / "fallback" 之一，
    只用于报告统计，不影响实际写进 sales.csv 的 brand 值。
    纯函数，不发请求，方便离线单测。
    """
    model_map = mapping.get("model_to_brand") or {}
    manufacturer_map = mapping.get("manufacturer_to_brand") or {}

    if model in model_map:
        return model_map[model], "model"
    if manufacturer in manufacturer_map:
        return manufacturer_map[manufacturer], "manufacturer"
    return manufacturer, "fallback"


def resolve_brand(model, manufacturer, mapping):
    brand, _source = resolve_brand_with_source(model, manufacturer, mapping)
    return brand


def label_rows(style_rows, body_type_map, ev_set, mapping, year, month):
    """
    以主榜 (style_rows) 为基准，用「车型名」左连接打上 body_type / brand / energy_type 标签。
    真正不在任何 body 榜单里的车型，body_type 填「其他」（不留空，避免前端图表出现无名分类）。
    body_type_map 传进来的时候已经是最终类别（轿车/MPV/SUV/运动汽车），不是原始 body 分类名。
    brand 按 resolve_brand_with_source 的优先级解析，同样绝不为空。
    这是一个纯函数（不发请求），方便离线单测。
    返回 (out_rows, other_count) —— other_count 是被填成「其他」的行数。
    """
    other_count = 0
    out_rows = []
    for r in style_rows:
        model = r["model"]
        manufacturer = r["manufacturer"]
        body_type = body_type_map.get(model, "其他")
        if body_type == "其他":
            other_count += 1
        energy_type = "新能源" if model in ev_set else "燃油"
        brand = resolve_brand(model, manufacturer, mapping)
        out_rows.append({
            "year": year,
            "month": month,
            "manufacturer": manufacturer,
            "brand": brand,
            "model": model,
            "body_type": body_type,
            "energy_type": energy_type,
            "sales": r["sales"],
        })
    return out_rows, other_count


def normalize_legacy_body_types(rows):
    """
    对已经在 sales.csv 里的存量行做原地规范化：旧版本用原始 body 分类名（两厢车/三厢车）
    存的行，改写成用户口径的「轿车」；其余取值（SUV/MPV/运动汽车/其他，以及已经是
    「轿车」的行）原样不动。

    幂等：跑多少次结果都一样——第二次跑的时候已经没有"两厢车"/"三厢车"这两个取值了，
    条件不命中，直接原样返回。不修改传入的 dict（返回新的列表+新的行 dict），避免
    意外的原地副作用。

    纯函数，不发请求，方便离线单测。返回 (normalized_rows, changed_count)。
    """
    legacy_values = {"两厢车", "三厢车"}
    normalized = []
    changed = 0
    for r in rows:
        if r.get("body_type") in legacy_values:
            new_r = dict(r)
            new_r["body_type"] = "轿车"
            normalized.append(new_r)
            changed += 1
        else:
            normalized.append(r)
    return normalized, changed


def normalize_legacy_brand_column(rows, mapping):
    """
    对存量行做原地规范化：老版本 sales.csv 根本没有 brand 这一列，csv.DictReader 读出来的
    行字典里压根没有 "brand" 这个 key；已经有 brand 的行（不管是这次新抓的、还是上次已经
    补过的）原样不动。

    幂等：第二次跑的时候所有行都已经有非空 brand 了，r.get("brand") 为真，条件不命中，
    直接原样返回。不修改传入的 dict。

    纯函数，不发请求，方便离线单测。返回 (normalized_rows, changed_count)。
    """
    normalized = []
    changed = 0
    for r in rows:
        if not r.get("brand"):
            new_r = dict(r)
            new_r["brand"] = resolve_brand(new_r.get("model", ""), new_r.get("manufacturer", ""), mapping)
            normalized.append(new_r)
            changed += 1
        else:
            normalized.append(r)
    return normalized, changed


def load_or_bootstrap_mapping():
    """
    读取品牌映射字典 data/mapping.json。
      - 文件存在 -> 直接读它（用户/coordinator 可能已经手工改过，绝不覆盖）。
      - 文件不存在 -> 用内置的 DEFAULT_MAPPING_JSON 写出一份占位字典，然后用它。
    解析失败时不让整个脚本崩掉，退回一个空字典（model_to_brand/manufacturer_to_brand
    都是空的，效果等价于"所有品牌都回退成厂商原值"），并把原因记到日志里。
    返回解析后的 dict，保证一定带有 "manufacturer_to_brand" / "model_to_brand" 两个键。
    """
    if os.path.exists(MAPPING_JSON_PATH):
        with open(MAPPING_JSON_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        log(f"读取已有品牌映射字典: {MAPPING_JSON_PATH}")
    else:
        os.makedirs(DATA_DIR, exist_ok=True)
        text = DEFAULT_MAPPING_JSON.strip() + "\n"
        with open(MAPPING_JSON_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"品牌映射字典不存在，已写出内置占位字典到 {MAPPING_JSON_PATH}（以后可以直接在仓库里编辑它）")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log(f"!! 品牌映射字典解析失败，本次运行回退为空字典（所有品牌都会回退成厂商原值）: {e}")
        data = {}

    if not isinstance(data, dict):
        data = {}
    data.setdefault("manufacturer_to_brand", {})
    data.setdefault("model_to_brand", {})
    return data


def compute_brand_stats(all_rows_sorted, mapping):
    """
    品牌映射统计：命中 model_to_brand / manufacturer_to_brand 的行数、回退为厂商原值的行数
    （附具体是哪些厂商，按行数从多到少排），合并后的品牌总数，品牌销量 Top20。

    直接用当前 mapping 对每一行重新判定命中来源，不依赖 CSV 里已经写好的 brand 值——
    这样报告反映的永远是"这次运行实际生效的字典"，不会因为字典更新了、但报告逻辑
    读的是旧标签而对不上。

    纯函数，不发请求，方便离线单测。
    """
    from collections import Counter

    source_counts = Counter()
    fallback_manufacturers = Counter()
    brand_sales = Counter()

    for r in all_rows_sorted:
        brand, source = resolve_brand_with_source(r["model"], r["manufacturer"], mapping)
        source_counts[source] += 1
        if source == "fallback":
            fallback_manufacturers[r["manufacturer"]] += 1
        brand_sales[brand] += r["sales"]

    return {
        "model_hits": source_counts.get("model", 0),
        "manufacturer_hits": source_counts.get("manufacturer", 0),
        "fallback_hits": source_counts.get("fallback", 0),
        "fallback_manufacturers": fallback_manufacturers.most_common(),
        "brand_count": len(brand_sales),
        "top20": brand_sales.most_common(20),
    }


def reconcile_categories(out_rows, body_lists):
    """
    质量守门 v2：用户口径的最终类别数（轿车/MPV/SUV/运动汽车）比原始 body 分类数少——
    两厢车+三厢车都并进轿车——所以对账基准不能再是"某一个 body 页面声明的共N条"，
    改成"该最终类别下，所有贡献它的 body 榜单实际解析出的车型名集合的并集大小"（去重），
    再和 out_rows 里这个类别实际的行数比较。

    比如轿车 = 两厢车榜单车型集合 ∪ 三厢车榜单车型集合；两边各自解析出5个车型、
    重叠2个，对账基准是 8（并集大小），不是 10（简单相加）。

    再做一次总量对账：各类别实际行数之和 + 「其他」行数 == 主榜(out_rows)总行数。

    返回 (results, total_check)。results 每条:
      {"final_category", "contributing": [(body_id, raw_name), ...],
       "page_side": 并集大小, "actual": out_rows里该类别实际行数,
       "mismatch": bool, "diff_models": [...]}

    纯函数，不发请求，方便离线单测（尤其是并集去重这条，是这次改动最容易写错的地方）。
    """
    from collections import Counter

    actual_counts = Counter(r["body_type"] for r in out_rows)

    groups = {}  # final_category -> {"contributing": [...], "models": set()}
    for body_id, raw_body_name, rows in body_lists:
        final_category = map_to_final_category(raw_body_name)
        g = groups.setdefault(final_category, {"contributing": [], "models": set()})
        g["contributing"].append((body_id, raw_body_name))
        g["models"].update(r["model"] for r in rows)

    results = []
    for final_category, g in groups.items():
        page_side = len(g["models"])  # 并集大小，去重后
        actual = actual_counts.get(final_category, 0)
        mismatch = page_side != actual
        diff_models = []
        if mismatch:
            output_models = {r["model"] for r in out_rows if r["body_type"] == final_category}
            diff_models = sorted((g["models"] - output_models) | (output_models - g["models"]))[:20]
        results.append({
            "final_category": final_category,
            "contributing": g["contributing"],
            "page_side": page_side, "actual": actual,
            "mismatch": mismatch, "diff_models": diff_models,
        })

    other_count = actual_counts.get("其他", 0)
    matched_sum = sum(r["actual"] for r in results)
    total_rows = len(out_rows)
    total_check_ok = (matched_sum + other_count) == total_rows

    return {
        "results": results,
        "total_check": {
            "matched_sum": matched_sum, "other_count": other_count,
            "total_rows": total_rows, "ok": total_check_ok,
        },
    }


# ---------------------------------------------------------------------------
# 幂等：读取已有 sales.csv
# ---------------------------------------------------------------------------

def load_existing_sales():
    if not os.path.exists(SALES_CSV):
        return [], set()
    rows = []
    months_done = set()
    with open(SALES_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["year"] = int(row["year"])
                row["month"] = int(row["month"])
                row["sales"] = int(row["sales"])
            except (KeyError, ValueError):
                continue
            rows.append(row)
            months_done.add((row["year"], row["month"]))
    return rows, months_done


def months_from_2024_01_to_now():
    now = datetime.now(timezone.utc)
    months = []
    y, m = 2024, 1
    while (y, m) <= (now.year, now.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def write_outputs(all_rows, report, mapping):
    os.makedirs(DATA_DIR, exist_ok=True)

    all_rows_sorted = sorted(all_rows, key=lambda r: (r["year"], r["month"], -r["sales"]))
    with open(SALES_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in all_rows_sorted:
            writer.writerow({k: r[k] for k in CSV_FIELDS})
    log(f"写入 {SALES_CSV} ({len(all_rows_sorted)} 行)")

    manufacturers = sorted({r["manufacturer"] for r in all_rows_sorted if r["manufacturer"]})
    with open(MANUFACTURERS_TXT, "w", encoding="utf-8") as f:
        for m in manufacturers:
            f.write(m + "\n")
    log(f"写入 {MANUFACTURERS_TXT} ({len(manufacturers)} 个厂商)")

    coverage = compute_body_type_coverage(all_rows_sorted)
    brand_stats = compute_brand_stats(all_rows_sorted, mapping)
    write_report(report, len(all_rows_sorted), len(manufacturers), coverage, brand_stats)


def compute_body_type_coverage(all_rows_sorted):
    """
    车体类型覆盖率 = 非「其他」的行数 / 总行数（「其他」现在是有意义的取值——代表
    "真的不在任何 body 榜单里"，不再是空字符串，所以覆盖率的分子要相应排除它）。
    额外给出「其他」的行数，以及最多 30 个「其他」车型样本（按"厂商 - 车型"去重展示）。
    """
    total = len(all_rows_sorted)
    other_count = sum(1 for r in all_rows_sorted if r["body_type"] == "其他")
    with_real_body_type = total - other_count
    rate = (with_real_body_type / total) if total else None

    unmatched_pairs = []
    seen = set()
    for r in all_rows_sorted:
        if r["body_type"] != "其他":
            continue
        key = (r["manufacturer"], r["model"])
        if key in seen:
            continue
        seen.add(key)
        unmatched_pairs.append(f"{r['manufacturer']} - {r['model']}")
        if len(unmatched_pairs) >= 30:
            break

    return {
        "total_rows": total,
        "with_real_body_type": with_real_body_type,
        "other_count": other_count,
        "rate": rate,
        "unmatched_samples": unmatched_pairs,
        "unmatched_total_distinct": len(seen) if len(unmatched_pairs) < 30 else None,
    }


def write_report(report, total_rows, total_manufacturers, coverage, brand_stats):
    lines = []
    lines.append("# 同步报告\n")
    lines.append(f"- 运行时间 (UTC): {report['started_at']} ~ {report['finished_at']}")
    lines.append(f"- 触发方式: {report.get('trigger_event', '未知')}")
    # notify_feishu.py 靠这一行做"新鲜度自检"：比对这里记录的运行编号和它自己环境里的
    # GITHUB_RUN_ID 是否一致，防止把上一次成功运行遗留下来的旧报告当成本次状态发出去。
    lines.append(f"- GITHUB_RUN_ID: {report.get('github_run_id', '未知（本地/离线运行）')}")
    lines.append(f"- 耗时: {report['elapsed_seconds']:.0f} 秒")
    lines.append(f"- max_months 本次生效值: {report.get('max_months_desc', '未知')}")
    lines.append(
        f"- force_refresh 本次生效值: {report.get('force_refresh', False)} "
        f"({report.get('force_refresh_desc', '未知')})"
    )
    lines.append(f"- 是否被拦截: {'是 - ' + report['blocked_reason'] if report.get('blocked_reason') else '否'}")
    lines.append(f"- 累计总行数 (sales.csv): {total_rows}")
    lines.append(f"- 累计厂商数 (manufacturers.txt): {total_manufacturers}")
    overwritten = report.get("overwritten_months", [])
    if overwritten:
        overwritten_str = ", ".join(f"{y}-{m:02d}" for y, m in overwritten)
        lines.append(f"- 本次重抓覆盖的月份 ({len(overwritten)} 个): {overwritten_str}")
    else:
        lines.append("- 本次重抓覆盖的月份: (无)")
    legacy_n = report.get("legacy_normalized_count", 0)
    lines.append(f"- 存量数据规范化：将 {legacy_n} 行「两厢车/三厢车」合并为「轿车」")
    legacy_brand_n = report.get("legacy_brand_backfilled_count", 0)
    lines.append(f"- 存量品牌补列：为 {legacy_brand_n} 行补上了 brand 列")
    lines.append("")

    lines.append("## 品牌映射\n")
    lines.append(f"- 命中 model_to_brand: {brand_stats['model_hits']} 行")
    lines.append(f"- 命中 manufacturer_to_brand: {brand_stats['manufacturer_hits']} 行")
    lines.append(f"- 回退为厂商原值: {brand_stats['fallback_hits']} 行")
    fallback_manus = brand_stats.get("fallback_manufacturers", [])
    if fallback_manus:
        lines.append("")
        lines.append(
            f"ℹ️ 有 {len(fallback_manus)} 个厂商未在字典中，已回退为厂商原值，建议补充字典："
        )
        for manu, cnt in fallback_manus:
            lines.append(f"- {manu} ({cnt} 行)")
    lines.append("")
    lines.append(f"- 合并后品牌总数: {brand_stats['brand_count']}")
    lines.append("")
    lines.append("### 品牌销量 Top 20\n")
    top20 = brand_stats.get("top20", [])
    if top20:
        lines.append("| 排名 | 品牌 | 销量 |")
        lines.append("|---|---|---|")
        for i, (brand, sales) in enumerate(top20, 1):
            lines.append(f"| {i} | {brand} | {sales} |")
    else:
        lines.append("(无数据)")
    lines.append("")

    lines.append("## 车体类型覆盖率\n")
    rate = coverage["rate"]
    if rate is None:
        lines.append("(sales.csv 目前是空的，无法计算覆盖率)")
    else:
        pct = rate * 100
        lines.append(
            f"- 非「其他」的行数 / 总行数: {coverage['with_real_body_type']} / {coverage['total_rows']} ({pct:.1f}%)"
        )
        lines.append(f"- 「其他」(真的不在任何 body 榜单里) 行数: {coverage['other_count']}")
        if rate < 0.9:
            lines.append("")
            lines.append("**⚠️ 覆盖率异常，请勿继续全量回补** —— 先确认 body-1..8 的探测表格和下面的「车体类型对账」，"
                          "看看是不是有分类 404/探测失败/对账不平，导致大量车型的 body_type 打成了「其他」。")
        lines.append("")
        if coverage["unmatched_samples"]:
            lines.append(f"「其他」车型样本（最多列 30 个，按 厂商-车型 去重）：\n")
            for s in coverage["unmatched_samples"]:
                lines.append(f"- {s}")
            if coverage.get("unmatched_total_distinct") is None:
                lines.append("- ...（还有更多，此列表截断到 30 个）")
    lines.append("")

    quality_gate_reasons = []
    if rate is not None and rate < 0.9:
        quality_gate_reasons.append(f"车体类型覆盖率 {rate * 100:.1f}% 低于 90%")
    for _rec in report.get("reconciliation", []):
        _ym = f"{_rec['year']}-{_rec['month']:02d}"
        for _r in _rec.get("results", []):
            if _r.get("mismatch"):
                quality_gate_reasons.append(f"{_ym} {_r['final_category']} 对账不平")
        if not _rec.get("total_check", {}).get("ok", True):
            quality_gate_reasons.append(f"{_ym} 总量对账不平")
    if quality_gate_reasons:
        lines.append("")
        lines.append(
            f"⚠️ **数据质量守门未通过**：{'；'.join(quality_gate_reasons)}。"
            "详见下方「车体类型对账」章节，建议人工核实后再信任本次数据。"
        )
        lines.append("")

    lines.append("## 车体类型对账\n")
    lines.append("以后每月自动跑的时候，这是发现数据源变化（比如某个 body 分类的编号/含义变了、"
                  "车型名在不同榜单间对不上）的第一道防线。")
    lines.append("")
    reconciliation = report.get("reconciliation", [])
    if not reconciliation:
        lines.append("(本次运行没有成功完成任何一个月份，无对账数据)")
    else:
        for rec in sorted(reconciliation, key=lambda x: (x["year"], x["month"])):
            yyyymm_label = f"{rec['year']}-{rec['month']:02d}"
            lines.append(f"### {yyyymm_label}\n")
            results = rec["results"]
            if results:
                lines.append("| 最终类别 | 贡献的 body 榜单 | 页面并集条数(去重) | 实际标上 | 差 | 状态 |")
                lines.append("|---|---|---|---|---|---|")
                for r in sorted(results, key=lambda x: x["final_category"]):
                    contributing_str = "+".join(f"body-{bid}({rn})" for bid, rn in r["contributing"])
                    diff = r["page_side"] - r["actual"]
                    status = "❌ 不平" if r["mismatch"] else "✅"
                    lines.append(
                        f"| {r['final_category']} | {contributing_str} | {r['page_side']} | "
                        f"{r['actual']} | {diff} | {status} |"
                    )
                mismatched = [r for r in results if r["mismatch"]]
                for r in mismatched:
                    diff = r["page_side"] - r["actual"]
                    contributing_str = "+".join(f"body-{bid}({rn})" for bid, rn in r["contributing"])
                    lines.append("")
                    lines.append(
                        f"⚠️ **{r['final_category']} 对账不平（{contributing_str}）：页面并集(去重) "
                        f"{r['page_side']} 条，实际标上 {r['actual']} 条，差 {diff} 条**"
                    )
                    if r["diff_models"]:
                        lines.append(f"差异涉及的车型（最多列 20 个）：{ '、'.join(r['diff_models']) }")
            else:
                lines.append("(本月没有任何成功探测到的 body 分类，无法对账)")

            tc = rec["total_check"]
            lines.append("")
            if tc["ok"]:
                lines.append(
                    f"✅ 总量对账通过：各分类实际行数之和 {tc['matched_sum']} + 「其他」{tc['other_count']} "
                    f"== 主榜总行数 {tc['total_rows']}"
                )
            else:
                lines.append(
                    f"⚠️ **总量对账不平：各分类实际行数之和 {tc['matched_sum']} + 「其他」{tc['other_count']} "
                    f"= {tc['matched_sum'] + tc['other_count']}，与主榜总行数 {tc['total_rows']} 不一致**"
                )
            lines.append("")

    conflicts = report.get("body_type_conflicts", [])
    if conflicts:
        lines.append("### 关联冲突\n")
        lines.append("同一个车型在同一个 body 榜单内重复出现——正常情况不该发生，"
                      "出现了说明数据源有异常（这不包括两厢/三厢/多形态同款车同时出现在多个 "
                      "body 榜单的情况，那个是预期行为，见下面「跨分类车型」）：")
        lines.append("")
        for c in conflicts:
            lines.append(f"- {c}")
        lines.append("")

    lines.append("## 跨分类车型\n")
    lines.append("出现在 2 个及以上 body 榜单里的车型（比如两厢版+三厢版同款车，或者少数真正"
                  "跨车身形态收录的车型），以及优先级裁决 (SUV > MPV > 轿车 > 运动汽车) 后"
                  "最终给了哪一类。以后数据源变了（比如多出一种新的跨榜方式），这里能第一时间看到。")
    lines.append("")
    cross_models = report.get("cross_category_models", {})
    if cross_models:
        lines.append("| 车型 | 出现在哪些榜 | 最终类别 | 出现月份数 |")
        lines.append("|---|---|---|---|")
        for model in sorted(cross_models):
            item = cross_models[model]
            occ_str = "、".join(f"body-{bid}({rn})" for bid, rn in item["occurrences"])
            lines.append(
                f"| {model} | {occ_str} | {item['final_category']} | {len(item['months_seen'])} |"
            )
    else:
        lines.append("(本次运行没有发现任何跨分类车型)")
    lines.append("")

    lines.append(f"## body-1..8 探测结果（本次试运行的核心产出）\n")
    body_probe = report.get("body_probe", [])
    if body_probe:
        lines.append("| 年月 | body 编号 | HTTP状态 | 提取到的中文分类名 | 共X条 | 实际解析行数 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in sorted(body_probe, key=lambda x: (x["year"], x["month"], x["body_id"])):
            http_status = p["http_status"] if p["http_status"] is not None else "-"
            name = p["name"] if p["name"] else "(未提取到/不存在)"
            total_ = p["total"] if p["total"] is not None else "-"
            parsed = p["parsed_rows"] if p["parsed_rows"] is not None else "-"
            note = p["error"] if p["error"] else ""
            lines.append(
                f"| {p['year']}-{p['month']:02d} | body-{p['body_id']} | {http_status} | "
                f"{name} | {total_} | {parsed} | {note} |"
            )
    else:
        lines.append("(本次运行没有成功进入任何一个月份的 body 探测阶段，见下方「放弃的月份」/「拦截」说明)")
    lines.append("")

    lines.append(f"## body-1..8 探测到的分类名称汇总\n")
    for bid, name in sorted(report.get("body_names", {}).items()):
        lines.append(f"- body-{bid}: {name}")
    if not report.get("body_names"):
        lines.append("(本次运行没有成功探测到任何 body 分类名称)")
    lines.append("")

    lines.append(f"## 本次运行新抓取的月份 ({len(report['months_done'])} 个)\n")
    if report["months_done"]:
        lines.append("| 年月 | 行数 | 未匹配车体类型行数 |")
        lines.append("|---|---|---|")
        for m in report["months_done"]:
            lines.append(f"| {m['year']}-{m['month']:02d} | {m['rows']} | {m['unmatched_body_type']} |")
    else:
        lines.append("(无 — 可能所有目标月份此前已抓取完毕，或本次运行未成功完成任何一个月)")
    lines.append("")
    lines.append(f"## 本次运行放弃的月份 ({len(report['months_aborted'])} 个)\n")
    for m in report["months_aborted"]:
        lines.append(f"- {m}")
    lines.append("")
    lines.append(f"## 请求失败记录 ({len(report['failures'])} 条)\n")
    for f_ in report["failures"][:200]:
        lines.append(f"- {f_}")
    if len(report["failures"]) > 200:
        lines.append(f"- ... 还有 {len(report['failures']) - 200} 条未列出")
    lines.append("")

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log(f"写入 {REPORT_MD}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def parse_max_months(raw):
    """
    解析 MAX_MONTHS 环境变量。

    约定（安全阀，保守优先）：
      - 未设置 / 空字符串（典型场景：push 触发，没有 workflow_dispatch inputs）-> 默认只跑 1 个月。
        注意这里 **不能** 用 `int(raw or 0)` 这种写法——那样空字符串会被当成 0（不限制），
        等于 push 一次就跑全量，正好是我们要防的事故，所以这里是显式的 if/else，不是那个写法。
      - '0' -> 不限制，跑完全部待抓取月份。
      - 正整数字符串 -> 最多跑这么多个月。
      - 解析不出数字 / 负数 -> 同样保守地按 1 个月处理，不报错、不跑全量。
    返回 (max_months:int, desc:str) —— desc 是给报告用的人类可读说明。
    """
    if raw is None or raw.strip() == "":
        return 1, "未设置/空 (push 触发或未传参) -> 保守默认只跑 1 个月"
    raw = raw.strip()
    try:
        val = int(raw)
    except ValueError:
        return 1, f"MAX_MONTHS='{raw}' 不是合法整数 -> 保守按 1 个月处理"
    if val < 0:
        return 1, f"MAX_MONTHS={val} 是负数，不合法 -> 保守按 1 个月处理"
    if val == 0:
        return 0, "MAX_MONTHS=0 -> 不限制，本次尝试跑完全部待抓取月份"
    return val, f"MAX_MONTHS={val} -> 本次最多跑 {val} 个月"


def parse_force_refresh(raw):
    """
    解析 FORCE_REFRESH 环境变量。和 parse_max_months 一样保守优先：
    只有明确写了 true/1/yes（大小写不敏感、去首尾空格）才是 True，
    其余一切——未设置、空字符串、'false'、拼写错误——都落到 False。
    push 触发时没有 inputs，FORCE_REFRESH 会是空字符串，必须落到 False（不重抓）。
    返回 (force_refresh: bool, desc: str)。
    """
    if raw is None or raw.strip() == "":
        return False, "未设置/空 (push 触发或未传参) -> 默认不重抓"
    normalized = raw.strip().lower()
    truthy = {"true", "1", "yes"}
    if normalized in truthy:
        return True, f"FORCE_REFRESH='{raw}' -> True，忽略已有月份，本次范围内月份全部重抓"
    return False, f"FORCE_REFRESH='{raw}' 不在 true/1/yes 之列 -> 保守按 False（不重抓）处理"


def compute_effective_months_done(months_done, force_refresh):
    """
    force_refresh=True 时，把"已经抓过的月份"这个概念清空，让范围内所有月份
    都重新进入待抓取列表；force_refresh=False 时维持原有的幂等跳过逻辑。
    纯函数，方便单测。
    """
    return set() if force_refresh else set(months_done)


def merge_existing_and_new(existing_rows, all_new_rows, synced_this_run):
    """
    按月粒度 upsert，而不是整表覆盖：
      - existing_rows 里凡是属于本次成功同步月份 (synced_this_run) 的行，一律丢弃——
        因为 all_new_rows 里已经有这些月份的新版本了，留着旧的会重复。
      - 不在 synced_this_run 里的月份（本次没跑、或者本次跑了但失败/被放弃）的旧数据
        原样保留，绝不因为本次运行不完整就被抹掉。
    这一条函数同时覆盖两种场景：
      - force_refresh=False 时，synced_this_run 只可能是"以前没抓过的新月份"，
        和 existing_rows 天然不交叉，这里的过滤等价于原来的"existing + new"逻辑，不变。
      - force_refresh=True 时，synced_this_run 可能和 existing_rows 有交叉，
        交叉的部分被新数据替换；本次抓取失败被放弃的月份不在 synced_this_run 里，
        原样保留，不受影响。
    纯函数，不发请求，方便离线单测（尤其是"半途放弃不能抹掉旧数据"这条）。
    """
    kept = [r for r in existing_rows if (r["year"], r["month"]) not in synced_this_run]
    return kept + all_new_rows


def main():
    import requests

    start_time = time.time()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "months_done": [],
        "months_aborted": [],
        "failures": [],
        "blocked_reason": None,
        "body_names": {},
        "body_probe": [],
        "reconciliation": [],
        "body_type_conflicts": [],
        "cross_category_models": {},
        "legacy_normalized_count": 0,
        "legacy_brand_backfilled_count": 0,
        # GitHub Actions 会把 github.event_name (schedule / workflow_dispatch / push) 通过
        # TRIGGER_EVENT 环境变量传进来；本地/离线运行时这个变量不存在，报告里如实标"未知"，
        # 不瞎猜。
        "trigger_event": os.environ.get("TRIGGER_EVENT", "未知（本地/离线运行）"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "未知（本地/离线运行）"),
    }

    max_months, max_months_desc = parse_max_months(os.environ.get("MAX_MONTHS"))
    report["max_months_desc"] = max_months_desc
    log(f"max_months 设置: {max_months_desc}")

    force_refresh, force_refresh_desc = parse_force_refresh(os.environ.get("FORCE_REFRESH"))
    report["force_refresh"] = force_refresh
    report["force_refresh_desc"] = force_refresh_desc
    log(f"force_refresh 设置: {force_refresh} ({force_refresh_desc})")

    # 品牌映射字典：存在就读，不存在就用内置占位字典自举写出一份（绝不覆盖已存在的文件）。
    mapping = load_or_bootstrap_mapping()
    log(f"品牌映射字典: manufacturer_to_brand {len(mapping.get('manufacturer_to_brand', {}))} 条, "
        f"model_to_brand {len(mapping.get('model_to_brand', {}))} 条")

    existing_rows, months_done = load_existing_sales()
    log(f"已有 {len(existing_rows)} 行历史数据，覆盖 {len(months_done)} 个月份")

    # 存量数据规范化：旧版本抓的行里 body_type 可能还是原始 body 分类名(两厢车/三厢车)，
    # 这里原地改写成用户口径的"轿车"。幂等，不需要用户 force_refresh 重跑 44 分钟。
    existing_rows, legacy_normalized_count = normalize_legacy_body_types(existing_rows)
    report["legacy_normalized_count"] = legacy_normalized_count
    if legacy_normalized_count:
        log(f"存量数据规范化: 将 {legacy_normalized_count} 行「两厢车/三厢车」合并为「轿车」")

    # 存量数据补 brand 列：旧版本 sales.csv 根本没有这一列，用当前字典原地补上。
    # 同样幂等，不需要用户 force_refresh 重跑。
    existing_rows, legacy_brand_backfilled_count = normalize_legacy_brand_column(existing_rows, mapping)
    report["legacy_brand_backfilled_count"] = legacy_brand_backfilled_count
    if legacy_brand_backfilled_count:
        log(f"存量数据补列: 为 {legacy_brand_backfilled_count} 行补上了 brand 列")

    effective_months_done = compute_effective_months_done(months_done, force_refresh)
    all_targets = [ym for ym in months_from_2024_01_to_now() if ym not in effective_months_done]
    if max_months > 0:
        targets = all_targets[:max_months]
    else:
        targets = all_targets
    log(f"本次待抓取月份 (共 {len(all_targets)} 个待抓，本次实际尝试 {len(targets)} 个): {targets}")

    all_new_rows = []
    body_names_cache = {}
    session = requests.Session()

    for (year, month) in targets:
        if time.time() - start_time > MAX_RUNTIME_SECONDS:
            log("接近时间预算上限，停止抓取新的月份")
            break
        try:
            rows = sync_month(session, year, month, report, body_names_cache, mapping)
            all_new_rows.extend(rows)
        except MonthAbortedException as e:
            log(f"!! 放弃 {year}-{month:02d}: {e}")
            report["months_aborted"].append(f"{year}-{month:02d}: {e}")
            continue
        except BlockedException as e:
            log(f"!! 触发拦截，停止全部后续请求: {e}")
            report["blocked_reason"] = str(e)
            break
        except Exception as e:
            log(f"!! {year}-{month:02d} 出现未预期异常: {e}")
            report["months_aborted"].append(f"{year}-{month:02d}: 未预期异常 {e}")
            report["failures"].append(traceback.format_exc())
            continue

    report["body_names"] = body_names_cache
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = time.time() - start_time

    # synced_this_run 是"本次真正成功产出了数据的月份"——只有这些月份的旧数据会被替换，
    # 本次没碰到、或者碰到了但失败/放弃/被拦截的月份，一律保留原样，见 merge_existing_and_new。
    synced_this_run = {(m["year"], m["month"]) for m in report["months_done"]}
    report["overwritten_months"] = sorted(synced_this_run & months_done)

    combined = merge_existing_and_new(existing_rows, all_new_rows, synced_this_run)
    write_outputs(combined, report, mapping)

    # 把本次运行"有没有值得关注的事情发生"暴露成 GitHub Actions 的 step output，
    # 供后面的"飞书通知"步骤据此判断要不要发——纯粹的幂等跳过（没抓到新月份、
    # 也没有任何失败/放弃/拦截）不该产生通知噪音；但凡有新数据、或者有任何
    # 失败/放弃/拦截，都算"值得关注"，交给 notify_feishu.py 自己去判断该发
    # 成功卡片还是告警卡片。
    months_synced_count = len(report["months_done"])
    months_aborted_count = len(report["months_aborted"])
    had_issues = bool(report.get("blocked_reason")) or months_aborted_count > 0
    noteworthy = months_synced_count > 0 or had_issues
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"months_synced_count={months_synced_count}\n")
                f.write(f"months_aborted_count={months_aborted_count}\n")
                f.write(f"had_issues={'true' if had_issues else 'false'}\n")
                f.write(f"noteworthy={'true' if noteworthy else 'false'}\n")
        except Exception as e:
            log(f"!! 写 GITHUB_OUTPUT 失败（不影响主流程）: {e}")
    log(
        f"本次运行小结: 新抓取 {months_synced_count} 个月, 放弃 {months_aborted_count} 个月, "
        f"noteworthy={noteworthy}"
    )

    log("同步结束")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, "FATAL_ERROR.txt"), "w", encoding="utf-8") as f:
                f.write(f"顶层未预期异常:\n{e}\n\n{traceback.format_exc()}")
        except Exception:
            print(traceback.format_exc())
        sys.exit(0)

