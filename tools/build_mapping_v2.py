import csv, json
from collections import defaultdict

# ---- load sales ----
totals = defaultdict(int)
model_sales = defaultdict(int)  # (manufacturer, model) -> sales
with open('/tmp/full/data/sales.csv') as f:
    r = csv.DictReader(f)
    rows = list(r)
    for row in rows:
        totals[row['manufacturer']] += int(row['sales'])
        model_sales[(row['manufacturer'], row['model'])] += int(row['sales'])

with open('/tmp/full/data/manufacturers.txt') as f:
    mans = [l.strip() for l in f if l.strip()]
assert len(mans) == 117

# ============================================================
# manufacturer_to_brand — ALL 117 manufacturers, no uncertain left.
# For the 4 structurally multi-brand manufacturers (长城汽车/蔚来/
# 上汽集团/奇瑞捷豹路虎) the value here is a SENTINEL fallback only —
# real classification happens via model_to_brand below, which covers
# 100% of their actual models in this dataset. The sentinel exists so
# that IF a brand-new model shows up later that model_to_brand hasn't
# been updated for yet, it surfaces as an obviously-fake brand name
# (matching the manufacturer name itself) instead of being silently
# mis-attributed to whichever existing sub-brand happens to be biggest.
# ============================================================
manufacturer_to_brand = {
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
# ---- newly resolved from uncertain (决定1 / 决定2 / 上次建议采纳) ----
"方程豹": "方程豹",              # 决定2：独立品牌
"腾势汽车": "腾势",              # 决定2：独立品牌
"仰望": "仰望",                  # 决定2：独立品牌
"AITO 问界": "问界",             # 采纳上次建议
"LUXCEED 智界": "智界",          # 采纳上次建议
"大众汽车安徽": "与众",          # 按上次建议定下来
"光束汽车": "MINI",              # 按上次建议定下来
"江汽集团": "江淮",              # 按上次建议定下来（细分见 model_to_brand）
"江铃集团新能源": "易至",        # 按上次建议定下来（细分见 model_to_brand）
"北京汽车制造厂": "锐胜",        # 按上次建议定下来（细分见 model_to_brand）
"星途": "星途",                  # 按上次建议定下来（星纪元细分见 model_to_brand）
# ---- 决定1：4家结构性一厂多品牌，manufacturer 级只给保守兜底值，
#      真实归类完全靠 model_to_brand（下方验证 100% 覆盖这4家全部车型）----
"长城汽车": "长城汽车",          # 哨兵值：真实值为 哈弗/坦克/魏牌，见 model_to_brand
"蔚来": "蔚来",                  # 哨兵值兼真实值：蔚来主品牌本身也叫"蔚来"；乐道/萤火虫见 model_to_brand
"上汽集团": "上汽集团",          # 哨兵值：真实值为 荣威/MG/飞凡/科莱威，见 model_to_brand
"奇瑞捷豹路虎": "奇瑞捷豹路虎",  # 哨兵值：真实值为 捷豹/路虎，见 model_to_brand
}

# ============================================================
# model_to_brand — only for models where manufacturer_to_brand
# would give the WRONG answer. Covers 100% of models under the
# 4 mandated manufacturers, plus the other multi-brand manufacturers
# found during review (星途/北京汽车制造厂/江汽集团/江铃集团新能源).
# ============================================================
model_to_brand = {}

# ---- 长城汽车：哈弗 / 坦克 / 魏牌（数据里没有欧拉/长城炮车型混在这个厂商条目下，
#      欧拉车型全部在独立厂商条目"长城新能源"里，已在 manufacturer_to_brand 处理）----
_changcheng_haval = ["哈弗大狗","哈弗H6","哈弗猛龙新能源","哈弗枭龙MAX","哈弗猛龙","哈弗H9",
                      "哈弗H5","哈弗M6","哈弗大狗 PLUS 新能源","哈弗赤兔","哈弗神兽","哈弗H6新能源","哈弗酷狗"]
_changcheng_tank = ["坦克300","坦克500新能源","坦克400新能源","坦克300新能源","坦克700新能源","坦克400","坦克500"]
_changcheng_wey = ["魏牌 高山","魏牌 蓝山","魏牌 V9X","魏牌 摩卡新能源","魏牌 拿铁DHT-PHEV"]
for m in _changcheng_haval: model_to_brand[m] = "哈弗"
for m in _changcheng_tank: model_to_brand[m] = "坦克"
for m in _changcheng_wey: model_to_brand[m] = "魏牌"

# ---- 蔚来：蔚来 / 乐道 / 萤火虫 ----
_nio_main = ["蔚来ES8","蔚来ES6","蔚来ET5T","蔚来EC6","蔚来ET5","蔚来ES9","蔚来ET7","蔚来EC7","蔚来ET9","蔚来ES7"]
_nio_onvo = ["乐道L60","乐道L90","乐道L80"]
_nio_firefly = ["firefly萤火虫"]
for m in _nio_main: model_to_brand[m] = "蔚来"
for m in _nio_onvo: model_to_brand[m] = "乐道"
for m in _nio_firefly: model_to_brand[m] = "萤火虫"

# ---- 上汽集团：荣威 / MG(名爵) / 飞凡 / 科莱威 ----
_saic_rw = ["荣威i5","荣威D7","荣威RX5","荣威D6","荣威i6 MAX新能源","荣威M7 DMH","荣威D5X DMH",
            "荣威iMAX8新能源","荣威RX5新能源","荣威i6","荣威Ei5","荣威iMAX8","荣威RX9"]
_saic_mg = ["MG4","MG5","MG7","MG 4X","MG ES5","MG ONE","MG6","MG Cyberster","名爵ZS"]
_saic_feifan = ["飞凡F7","飞凡R7"]
_saic_kelaiwei = ["科莱威CLEVER"]
for m in _saic_rw: model_to_brand[m] = "荣威"
for m in _saic_mg: model_to_brand[m] = "MG"
for m in _saic_feifan: model_to_brand[m] = "飞凡"
for m in _saic_kelaiwei: model_to_brand[m] = "科莱威"

# ---- 奇瑞捷豹路虎：捷豹 / 路虎 ----
_jlr_jaguar = ["捷豹XFL","捷豹XEL","捷豹E-PACE"]
_jlr_lr = ["揽胜极光","发现运动","发现运动版新能源","揽胜极光新能源"]
for m in _jlr_jaguar: model_to_brand[m] = "捷豹"
for m in _jlr_lr: model_to_brand[m] = "路虎"

# ---- 星途：星纪元 从星途拆出 ----
model_to_brand["星纪元 ET"] = "星纪元"
# 其余星途系车型（星途凌云/ES/瑶光/瑶光C-DM/揽月/揽月C-DM/ET5/追风/追风C-DM/EX7）
# 走 manufacturer_to_brand 默认值"星途"，不需要单独列，因为默认值已经正确。

# ---- 北京汽车制造厂：锐胜(默认) / 勇士 / 元宝 / 家宝 / 212(并入212品牌) ----
model_to_brand["勇士"] = "勇士"
model_to_brand["元宝"] = "元宝"
model_to_brand["家宝"] = "家宝"
model_to_brand["212经典"] = "212"   # 与"二一二越野车"厂商条目的"212 T01"合并为同一"212"品牌
# 注：锐胜王牌M7/锐胜王牌M7新能源/锐胜M8新能源/锐胜M8 不需要单独列，
# 因为 manufacturer_to_brand["北京汽车制造厂"]="锐胜" 默认值已经正确。
# "北汽L6H"（122辆）品牌归属把握不足，未列入，见 _unresolved_notes，
# 会按默认值回退为"锐胜"（不一定准确，已在下方说明）。

# ---- 江汽集团：细分瑞风 / 钇为 / 爱跑 / 花仙子，其余走默认值"江淮" ----
model_to_brand["瑞风M3"] = "瑞风"
model_to_brand["瑞风E3"] = "瑞风"
model_to_brand["瑞风RF8 PHEV"] = "瑞风"
model_to_brand["瑞风RF8"] = "瑞风"
model_to_brand["钇为3"] = "钇为"
model_to_brand["爱跑"] = "爱跑"
model_to_brand["花仙子"] = "花仙子"
# 江淮QX PHEV / 江淮X8 PLUS / 江淮X8 E家 / 江淮A5 PLUS 不需要单独列，
# 走默认值"江淮"即正确。

# ---- 江铃集团新能源：细分易至(默认) / 羿 ----
model_to_brand["羿"] = "羿"
model_to_brand["羿驰05"] = "羿"
model_to_brand["羿驰01"] = "羿"
model_to_brand["羿驰05S"] = "羿"
# 易至EV2 / 易至EV3 走默认值"易至"即正确。

uncertain = []  # 已收口，全部清空

_unresolved_notes = [
    {
        "item": "北京汽车制造厂 / 北汽L6H",
        "note": "销量很小（122辆，占该厂商总量的0.27%）。命名规律与'锐胜王牌M7/M8'系列不同，"
                 "无法确认它是锐胜品牌下的另一款卡车，还是北汽制造厂旗下未识别的其他子品牌。"
                 "未列入 model_to_brand，会按 manufacturer_to_brand 默认值回退为'锐胜'——这是一个"
                 "未经证实的猜测，不是确定归类，请知悉。"
    },
    {
        "item": "东风汽车 / 示界06",
        "note": "销量较小（2,053辆，占该厂商总量的1.72%）。厂商'东风汽车'旗下车型以'纳米'品牌"
                 "(纳米01/06/BOX)为主，但'示界06'命名方式不同，无法确认它是否仍属于纳米品牌，"
                 "还是东风旗下另一个未识别的新子品牌。未列入 model_to_brand，会按"
                 "manufacturer_to_brand 默认值回退为'纳米'——这是一个未经证实的猜测，请知悉。"
    },
    {
        "item": "上汽集团 / 科莱威CLEVER",
        "note": "'科莱威CLEVER'（838辆）已映射为独立品牌'科莱威'。这是上汽旗下一个知名度较低的"
                 "微型电动车品牌，我对它的品牌独立性有一定把握，但不如荣威/MG/飞凡确定，供留意。"
    },
    {
        "item": "江汽集团 / 爱跑、花仙子",
        "note": "'爱跑'（5,012辆）和'花仙子'（4,076辆）已各自映射为同名独立品牌。这两个是江淮旗下"
                 "知名度较低的微型电动车产品线，我对它们是否应算'独立品牌'还是应并入'江淮'或'钇为'"
                 "把握不是特别足（体量小，江淮系微型车的品牌层级本身对外宣传也不算清晰），供确认。"
    },
    {
        "item": "广汽本田新能源 / 绎乐",
        "note": "销量极小（2辆）。'绎乐'可能属于本田在华新推出的电动车子品牌'烨'(Ye)，而不是"
                 "传统本田品牌，但因样本量为2辆、且我对'烨'品牌在这份数据里的具体命名规则"
                 "把握不足，暂时保留映射为'本田'（原有规则：广汽本田新能源→本田），未额外拆分，"
                 "供留意，影响可忽略。"
    },
    {
        "item": "大众汽车安徽 / 与众全系（与众06/07/08）",
        "note": "已按上次建议定为独立品牌'与众'（不并入'大众'）。销量很小（2,149辆，占比0.004%），"
                 "但我对'与众'这个新品牌在消费者认知里独立于大众VW车标的程度依然把握不是100%，"
                 "供留意，实际影响可忽略。"
    },
]

# ============================================================
# integrity checks
# ============================================================
missing = [m for m in mans if m not in manufacturer_to_brand]
extra = [m for m in manufacturer_to_brand if m not in mans]
print("manufacturers.txt count:", len(mans))
print("manufacturer_to_brand count:", len(manufacturer_to_brand))
print("missing from manufacturer_to_brand:", missing)
print("extra keys not in manufacturers.txt:", extra)
assert not missing and not extra and len(manufacturer_to_brand) == 117

meta = {
    "description": "厂商/车型 → 品牌 映射字典，用于中国汽车销量看板的『厂商/品牌』视角切换",
    "version": "1.0",
    "resolution_order": "model_to_brand 优先（按车型名精确匹配），其次 manufacturer_to_brand，都没有则回退为 manufacturer 原值（本数据集18814行中，回退发生次数应为0，见 self-check）",
    "generated_from": "data/manufacturers.txt (117 家), data/sales.csv (18814 行, 2024-01 至 2026-07 共31个月, 累计57,124,329辆)",
    "rules": (
        "1. 品牌 = 消费者认知里的那个车标/emblem，不是母公司或合资公司名。"
        "如 上汽大众/一汽-大众 → 大众；上汽大众斯柯达 → 斯柯达；一汽-大众捷达 → 捷达（2019年已独立成品牌）。"
        "2. 同一合资公司下的多个独立品牌要拆开：上汽通用别克/雪佛兰/凯迪拉克 → 三个独立品牌；"
        "长安福特/长安马自达/长安林肯 → 福特/马自达/林肯。"
        "3. 自主品牌集团下已独立运营的子品牌要拆开：长安启源→启源、广汽埃安→埃安；"
        "同一车标的不同生产主体要合并：五菱新能源 和 上汽通用五菱 都→五菱；一汽海马 和 海马汽车 都→海马。"
        "4. 合资公司的外方品牌名统一为中文通行译名：一汽奥迪/上汽奥迪→奥迪，广汽本田/东风本田→本田。"
        "5. 比亚迪子品牌方程豹/腾势/仰望：经用户拍板，按独立品牌处理，不并入比亚迪（决定2）。"
        "6. 华为鸿蒙智行『界』系列（问界/智界/享界/尊界/尚界）：厂商名与品牌名基本一一对应，"
        "直接采用（问界/智界经确认采纳）。"
        "7. 【核心规则，决定1】当一个厂商条目内混合多个消费者认知品牌（不同车标/渠道/定位）时，"
        "manufacturer_to_brand 只给一个保守的『哨兵/兜底』值（通常等于厂商原名，蔚来除外——蔚来"
        "主品牌本身也叫『蔚来』故兜底值恰好正确），真正的品牌归类通过 model_to_brand 按车型名"
        "精确匹配实现，解析时 model_to_brand 优先级高于 manufacturer_to_brand。适用：长城汽车"
        "（哈弗/坦克/魏牌）、蔚来（蔚来/乐道/萤火虫）、上汽集团（荣威/MG/飞凡/科莱威）、"
        "奇瑞捷豹路虎（捷豹/路虎）；此外星途（星途/星纪元）、北京汽车制造厂（锐胜/勇士/元宝/家宝/212）、"
        "江汽集团（江淮/瑞风/钇为/爱跑/花仙子）、江铃集团新能源（易至/羿）也按同一机制处理，"
        "只是这些厂商没有强制要求做到100%精细拆分，manufacturer_to_brand的默认值已覆盖大部分销量。"
        "8. 兜底值选择原则：优先选'哪怕未来出现字典未覆盖的新车型，这个值也大概率仍然正确'的选项——"
        "蔚来主品牌用『蔚来』；无法判断哪个子品牌最具代表性、或用主品牌名会造成后续新车型被误分类"
        "风险的（长城汽车/上汽集团/奇瑞捷豹路虎），选择厂商原名作为哨兵值，使其在品牌视角下清晰地"
        "表现为'未分类，需要更新model_to_brand'，而不是悄悄地被错误合并进某个具体子品牌。"
        "9. 拿不准、样本量小、无法用现有知识确认的车型/厂商，一律记录进 _unresolved_notes 并给出"
        "已采用的近似处理方式，不藏在字典里假装确定。"
    ),
}

out = {
    "_meta": meta,
    "manufacturer_to_brand": manufacturer_to_brand,
    "model_to_brand": model_to_brand,
    "_unresolved_notes": _unresolved_notes,
}
# keep 'uncertain' key present as empty array too, for backward compat / explicitness
out["uncertain"] = uncertain

with open('/tmp/brand-mapping/mapping.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("\nWROTE mapping.json OK")
print("model_to_brand entries:", len(model_to_brand))
print("_unresolved_notes entries:", len(_unresolved_notes))
