#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_scope_filter.py
独立回归测试脚本：验收汽车销量看板 HTML（归属筛选 / 全部车体类型 / 下钻 等改造）的功能正确性。

用法:
    python3 verify_scope_filter.py [--html <路径>] [--shots <目录>] [--json <输出路径>]

退出码: 全部 PASS -> 0；存在 FAIL -> 1。

设计要点（见交付说明里的“假设”一节）：
  - 页面用一个 IIFE 包裹全部逻辑，`state` / `RAW` / `lastUniverse` 等变量【不会】挂到 window 上
    （已用 typeof window.RAW 等实测确认，无论改造前后大概率都是如此，因为这是既有的封装写法，
    不属于本次改造范围）。因此本脚本不依赖 page.evaluate() 读内部闭包状态，而是：
      1) 从 HTML 源码里正则提取内嵌的 `var RAW = {...};` / `var META = {...};` 两个 JSON 字面量，
         在 Python 侧独立复刻页面的 ymIndex / lastMonthOfYear / YTD 累计等口径逻辑，算出“期望值”；
      2) 所有“用户可见结果”一律用 DOM 文本 或 echarts 的 getOption().series 断言，不信任何内部状态。
  - 这样即使 RAW 的字段结构在改造后完全不变（预期如此，因为 build.py 的数据生成部分不属于本次
    DOM/交互改造范围），断言依然独立可靠；如果 RAW 结构变了导致正则/JSON 解析失败，脚本会把相关
    用例标记为 FAIL 并给出解析错误详情，不会让整个脚本崩溃。
"""

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("缺少 playwright 包，请先运行: pip install playwright --break-system-packages"
          "（不要运行 playwright install，浏览器已预装）", file=sys.stderr)
    sys.exit(2)


# ============================================================
# 结果收集
# ============================================================

class Results:
    def __init__(self):
        self.items = []  # list of dict

    def record(self, id_, name, status, expected=None, actual=None, detail=None):
        self.items.append({
            "id": id_,
            "name": name,
            "status": status,  # PASS / FAIL / N/A
            "expected": expected,
            "actual": actual,
            "detail": detail,
        })
        return status

    def has_fail(self):
        return any(it["status"] == "FAIL" for it in self.items)

    def print_table(self):
        print()
        print("=" * 100)
        print(f"{'编号':<5}{'结果':<6}{'名称'}")
        print("=" * 100)
        for it in self.items:
            mark = {"PASS": "PASS", "FAIL": "FAIL", "N/A": "N/A "}[it["status"]]
            print(f"{it['id']:<5}{mark:<6}{it['name']}")
            if it["status"] != "PASS":
                if it["expected"] is not None or it["actual"] is not None:
                    print(f"       期望: {it['expected']}")
                    print(f"       实际: {it['actual']}")
                if it["detail"]:
                    print(f"       详情: {it['detail']}")
        print("=" * 100)
        total = len(self.items)
        n_pass = sum(1 for it in self.items if it["status"] == "PASS")
        n_fail = sum(1 for it in self.items if it["status"] == "FAIL")
        n_na = sum(1 for it in self.items if it["status"] == "N/A")
        print(f"总计 {total}   PASS {n_pass}   FAIL {n_fail}   N/A {n_na}")
        print("=" * 100)

    def to_json(self):
        return json.dumps({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "results": self.items,
            "summary": {
                "total": len(self.items),
                "pass": sum(1 for it in self.items if it["status"] == "PASS"),
                "fail": sum(1 for it in self.items if it["status"] == "FAIL"),
                "na": sum(1 for it in self.items if it["status"] == "N/A"),
            }
        }, ensure_ascii=False, indent=2)


R = Results()


def safe_run(id_, name, fn):
    """运行一个测试函数；捕获任何异常，转成 FAIL，不中断整个脚本。"""
    try:
        fn()
    except AssertionError as e:
        R.record(id_, name, "FAIL", detail=f"断言失败: {e}")
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        R.record(id_, name, "FAIL", detail=f"用例执行异常: {e}\n{tb}")


# ============================================================
# 从 HTML 源码提取 RAW / META，并在 Python 侧复刻口径逻辑
# ============================================================

def extract_raw_meta(html_text):
    raw, meta = None, None
    err = []
    m = re.search(r'var RAW = (\{.*?\});', html_text, re.S)
    if m:
        try:
            raw = json.loads(m.group(1))
        except Exception as e:
            err.append(f"RAW JSON 解析失败: {e}")
    else:
        err.append("未找到 var RAW = {...}; 声明")
    m2 = re.search(r'var META = (\{.*?\});', html_text, re.S)
    if m2:
        try:
            meta = json.loads(m2.group(1))
        except Exception as e:
            err.append(f"META JSON 解析失败: {e}")
    else:
        err.append("未找到 var META = {...}; 声明")
    return raw, meta, err


class DataOracle:
    """在 Python 侧复刻页面的数据口径（ymIndex / lastMonthOfYear / YTD 累计），
    作为跟 DOM/图表断言比对的“期望值”来源。"""

    def __init__(self, raw, meta):
        self.raw = raw
        self.meta = meta
        self.n_months = raw["nMonths"]

    def ym_index(self, year, month):
        return (year - 2024) * 12 + (month - 1)

    def last_month_of_year(self, year):
        idx = min(12, self.n_months - (year - 2024) * 12)
        return max(0, idx)

    def ytd(self, farr, earr, year, cap_month, energy="all"):
        s = 0
        for m in range(1, cap_month + 1):
            idx = self.ym_index(year, m)
            if 0 <= idx < self.n_months:
                f = farr[idx] or 0
                e = earr[idx] or 0
                if energy == "fuel":
                    s += f
                elif energy == "ev":
                    s += e
                else:
                    s += f + e
        return s

    def manu_idx(self, name):
        return self.raw["manu"]["n"].index(name)

    def brand_idx(self, name):
        return self.raw["brand"]["n"].index(name)

    def models_with_sales_count(self, year, body_type_idx=None, manu_name=None,
                                 brand_name=None, energy="all"):
        """复刻 currentDim() 的过滤条件（车体类型 + 归属），但额外要求 ytd>0
        （即“当年有销量的车型”）。返回 (count, names_with_sales, all_owned_names)"""
        cap = self.last_month_of_year(year)
        manu_i = self.manu_idx(manu_name) if manu_name else None
        brand_i = self.brand_idx(brand_name) if brand_name else None
        names_with_sales = []
        all_owned_names = []
        for i in range(len(self.raw["model"]["n"])):
            if body_type_idx is not None and self.raw["modelBody"][i] != body_type_idx:
                continue
            if manu_i is not None and self.raw["modelManu"][i] != manu_i:
                continue
            if brand_i is not None and self.raw["modelBrand"][i] != brand_i:
                continue
            all_owned_names.append(self.raw["model"]["n"][i])
            y = self.ytd(self.raw["model"]["f"][i], self.raw["model"]["e"][i], year, cap, energy)
            if y > 0:
                names_with_sales.append(self.raw["model"]["n"][i])
        return len(names_with_sales), names_with_sales, all_owned_names

    def owned_model_name_set(self, manu_name=None, brand_name=None):
        """不考虑年份/销量，只按归属映射返回全部车型名集合（用于交叉验证“图例名是否属于该归属”）。"""
        manu_i = self.manu_idx(manu_name) if manu_name else None
        brand_i = self.brand_idx(brand_name) if brand_name else None
        out = set()
        for i in range(len(self.raw["model"]["n"])):
            if manu_i is not None and self.raw["modelManu"][i] != manu_i:
                continue
            if brand_i is not None and self.raw["modelBrand"][i] != brand_i:
                continue
            out.add(self.raw["model"]["n"][i])
        return out

    def entity_ytd(self, kind, name, year, energy="all"):
        """kind: 'manu' | 'brand' -> 该实体自身（非车型）的 YTD。"""
        cap = self.last_month_of_year(year)
        dim = self.raw[kind]
        i = dim["n"].index(name)
        return self.ytd(dim["f"][i], dim["e"][i], year, cap, energy)

    def yoy_pct(self, kind, name, year, energy="all"):
        # 关键：同比必须用“当前年份实际可得的月数”同时截断今年和去年两侧再比较
        # （即页面里 computeUniverseAt 的口径），不能各自用各自那一年的 lastMonthOfYear，
        # 否则会出现“今年7个月 vs 去年12个月”的跨期比较错误 —— 这正是 G1 要防的回归。
        cap = self.last_month_of_year(year)
        dim = self.raw[kind]
        i = dim["n"].index(name)
        cur = self.ytd(dim["f"][i], dim["e"][i], year, cap, energy)
        prev = self.ytd(dim["f"][i], dim["e"][i], year - 1, cap, energy)
        if prev <= 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    # ---- H/J 组新增的口径复刻 ----

    def pool_count_with_sales(self, kind, year, energy="all"):
        """厂商/品牌粒度的“当年有销量对象数”（复刻修正后的 computeUniverse：ytd>0 才计入池子）。
        kind: 'manu' | 'brand'。返回 (count, names_with_sales)。"""
        cap = self.last_month_of_year(year)
        dim = self.raw[kind]
        names = []
        for i in range(len(dim["n"])):
            y = self.ytd(dim["f"][i], dim["e"][i], year, cap, energy)
            if y > 0:
                names.append(dim["n"][i])
        return len(names), names

    def model_index(self, name):
        if not hasattr(self, "_model_idx_cache"):
            self._model_idx_cache = {n: i for i, n in enumerate(self.raw["model"]["n"])}
        return self._model_idx_cache.get(name)

    def model_ytd_by_name(self, name, year, energy="all"):
        """车型粒度：按名字查 YTD（不看车体类型/归属，仅用于校验“图例上有没有0销量车型”这类
        跟车体类型/归属筛选无关的通用检查）。名字不存在时返回 None。"""
        i = self.model_index(name)
        if i is None:
            return None
        cap = self.last_month_of_year(year)
        return self.ytd(self.raw["model"]["f"][i], self.raw["model"]["e"][i], year, cap, energy)

    def entity_ytd_by_kind_name(self, kind, name, year, energy="all"):
        """通用版：kind='manu'/'brand' 走 entity_ytd；kind='model' 走 model_ytd_by_name。
        名字找不到时返回 None（调用方应视为异常情况，不能默默当 0 处理）。"""
        if kind == "model":
            return self.model_ytd_by_name(name, year, energy)
        dim = self.raw[kind]
        if name not in dim["n"]:
            return None
        return self.entity_ytd(kind, name, year, energy)

    def all_models_monthly_totals(self, year, energy="all"):
        """全部 895 款车型（不做任何车体类型/归属过滤）按月累计求和的 YTD 序列（1..lastM 月，
        cum[m] = 截至第 m 月的累计）。用于 J8 校验“其他”聚合线的口径：因为单月销量不可能为负，
        任何“当年 ytd<=0”被 computeUniverse 过滤掉的车型，其每个月的值本来就是 0，
        对月度合计毫无影响——所以“全部池子(721) 的月度合计”与“全部895款车型的月度合计”
        必然相等，用后者算更省事、也更独立于被测代码的具体过滤实现。"""
        cap = self.last_month_of_year(year)
        n = self.raw["model"]["n"]
        monthly = [0.0] * cap
        for i in range(len(n)):
            f = self.raw["model"]["f"][i]
            e = self.raw["model"]["e"][i]
            for m in range(1, cap + 1):
                idx = self.ym_index(year, m)
                if 0 <= idx < self.n_months:
                    fv = f[idx] or 0
                    ev = e[idx] or 0
                    if energy == "fuel":
                        monthly[m - 1] += fv
                    elif energy == "ev":
                        monthly[m - 1] += ev
                    else:
                        monthly[m - 1] += fv + ev
        cum = []
        run = 0.0
        for v in monthly:
            run += v
            cum.append(run)
        return cum

    def model_monthly_cum(self, name, year, energy="all"):
        """单个车型按月累计 YTD 序列（1..lastM 月），用于 J8 从总量里减去已展示对象。"""
        i = self.model_index(name)
        if i is None:
            return None
        cap = self.last_month_of_year(year)
        f = self.raw["model"]["f"][i]
        e = self.raw["model"]["e"][i]
        monthly = []
        for m in range(1, cap + 1):
            idx = self.ym_index(year, m)
            if 0 <= idx < self.n_months:
                fv = f[idx] or 0
                ev = e[idx] or 0
                if energy == "fuel":
                    monthly.append(fv)
                elif energy == "ev":
                    monthly.append(ev)
                else:
                    monthly.append(fv + ev)
            else:
                monthly.append(0)
        cum = []
        run = 0.0
        for v in monthly:
            run += v
            cum.append(run)
        return cum


# ============================================================
# Playwright DOM 辅助函数
# ============================================================

def q(page, sel):
    return page.query_selector(sel)


def qa(page, sel):
    return page.query_selector_all(sel)


def inner_text_or_none(page, sel):
    el = q(page, sel)
    if el is None:
        return None
    try:
        return el.inner_text()
    except Exception:
        return None


def click_chip(page, group_sel, attr, value):
    el = q(page, f'{group_sel} .chip[{attr}="{value}"]')
    if el is None:
        raise AssertionError(f"找不到 chip: {group_sel} [{attr}={value}]")
    el.click()
    page.wait_for_timeout(200)


def set_gran(page, gran):
    click_chip(page, "#granChips", "data-gran", gran)


def set_energy(page, energy):
    click_chip(page, "#energyChips", "data-energy", energy)


def set_year(page, year):
    click_chip(page, "#yearChips", "data-year", str(year))


def get_select_options(page, sel):
    el = q(page, sel)
    if el is None:
        return None
    return page.eval_on_selector(
        sel,
        "el => Array.from(el.options).map(o => ({value:o.value, text:o.textContent, selected:o.selected}))"
    )


def get_select_value(page, sel):
    el = q(page, sel)
    if el is None:
        return None
    return page.eval_on_selector(sel, "el => el.value")


def set_select(page, sel, value):
    el = q(page, sel)
    if el is None:
        raise AssertionError(f"找不到下拉框: {sel}")
    opts = get_select_options(page, sel) or []
    if not any(o["value"] == value for o in opts):
        raise AssertionError(f"下拉框 {sel} 没有 value={value!r} 的选项（现有: "
                              f"{[o['value'] for o in opts]}）")
    page.select_option(sel, value, timeout=3000)
    page.wait_for_timeout(250)


def get_optgroup_structure(page, sel):
    el = q(page, sel)
    if el is None:
        return None
    return page.eval_on_selector(
        sel,
        """el => Array.from(el.children).map(c => {
            if (c.tagName === 'OPTGROUP') {
                return {tag:'OPTGROUP', label:c.label,
                        options: Array.from(c.children).map(o=>({value:o.value, text:o.textContent}))};
            }
            return {tag:'OPTION', value:c.value, text:c.textContent};
        })"""
    )


def is_visible(page, sel):
    el = q(page, sel)
    if el is None:
        return False
    try:
        return el.is_visible()
    except Exception:
        return False


def legend_items(page):
    el = q(page, "#legendList")
    if el is None:
        return None
    return page.eval_on_selector_all(
        "#legendList .legend-item",
        """els => els.map(el => ({
            key: el.getAttribute('data-key'),
            name: el.querySelector('.name') ? el.querySelector('.name').textContent : null,
            checked: el.querySelector('input[type=checkbox]') ? el.querySelector('input[type=checkbox]').checked : null
        }))"""
    )


def legend_names(page):
    items = legend_items(page)
    return [it["name"] for it in items] if items is not None else None


def legend_checked_names(page):
    items = legend_items(page)
    if items is None:
        return None
    return [it["name"] for it in items if it["checked"]]


def click_legend_checkbox_by_name(page, name):
    row = page.locator(f'#legendList .legend-item:has(.name)').filter(has_text=name)
    if row.count() == 0:
        raise AssertionError(f"图例里找不到: {name}")
    row.first.locator('input[type=checkbox]').click()
    page.wait_for_timeout(150)


def click_legend_checkbox_by_exact_name(page, name):
    """跟 click_legend_checkbox_by_name 的区别：按精确名字匹配（Playwright 的 has_text 是子串匹配，
    “红旗H5”会连“红旗H5 PHEV”一起命中，J 组这种同前缀车型名必须精确匹配）。"""
    row = page.locator(f'#legendList .legend-item:has(.name:text-is("{name}"))')
    if row.count() == 0:
        raise AssertionError(f"图例里找不到精确匹配: {name}")
    row.first.locator('input[type=checkbox]').click()
    page.wait_for_timeout(150)


def open_drawer_by_exact_name(page, name):
    row = page.locator(f'#legendList .legend-item:has(.name:text-is("{name}"))')
    if row.count() == 0:
        return False
    row.first.locator('.name').click()
    page.wait_for_timeout(350)
    return True


def open_drawer_by_name(page, name):
    row = page.locator('#legendList .legend-item').filter(has_text=name)
    if row.count() == 0:
        return False
    row.first.locator('.name').click()
    page.wait_for_timeout(350)
    return True


def close_drawer(page):
    el = q(page, "#drawerClose")
    if el is not None:
        el.click()
        page.wait_for_timeout(200)


def drawer_is_open(page):
    """#drawer 用 CSS transform 滑出/滑入（一直 display:flex，不是 display:none），
    所以不能用 Playwright 的 is_visible() 判断是否“打开”——那只看 display/尺寸，
    看不出 transform 把它推到屏幕外。这里直接读 classList 是否含 'open'。"""
    el = q(page, "#drawer")
    if el is None:
        return None
    return page.eval_on_selector("#drawer", "el => el.classList.contains('open')")


def get_echarts_series(page):
    """返回 [{id, name}, ...]，或 None（元素不存在 / echarts 未初始化），或抛异常信息 dict。"""
    if q(page, "#chart") is None:
        return None
    return page.evaluate(
        """() => {
            try {
                const dom = document.getElementById('chart');
                if (typeof echarts === 'undefined') return {__error__: 'echarts undefined'};
                const inst = echarts.getInstanceByDom(dom);
                if (!inst) return {__error__: 'no echarts instance'};
                const opt = inst.getOption();
                return (opt.series || []).map(s => ({id: s.id, name: s.name}));
            } catch (e) { return {__error__: String(e)}; }
        }"""
    )


def get_echarts_series_full(page):
    """跟 get_echarts_series 一样，但额外带上 data（累计值数组），供 J8 逐月核对“其他”线用。"""
    if q(page, "#chart") is None:
        return None
    return page.evaluate(
        """() => {
            try {
                const dom = document.getElementById('chart');
                if (typeof echarts === 'undefined') return {__error__: 'echarts undefined'};
                const inst = echarts.getInstanceByDom(dom);
                if (!inst) return {__error__: 'no echarts instance'};
                const opt = inst.getOption();
                return (opt.series || []).map(s => ({id: s.id, name: s.name, data: s.data}));
            } catch (e) { return {__error__: String(e)}; }
        }"""
    )


def legend_ranks(page):
    """按当前 DOM 顺序读取图例每一项的 rank 数字（.rank 文本形如 '#3'），用于 H6 连续性检查。"""
    el = q(page, "#legendList")
    if el is None:
        return None
    return page.eval_on_selector_all(
        "#legendList .legend-item .rank",
        "els => els.map(el => el.textContent)"
    )


def get_drawer_stat_tiles(page):
    el = q(page, "#drawerStats")
    if el is None:
        return None
    return page.eval_on_selector_all(
        "#drawerStats .stat-tile",
        """els => els.map(el => ({
            lbl: el.querySelector('.lbl') ? el.querySelector('.lbl').textContent : null,
            val: el.querySelector('.val') ? el.querySelector('.val').textContent : null,
            delta: el.querySelector('.delta') ? el.querySelector('.delta').textContent : null
        }))"""
    )


def find_tile(tiles, keyword):
    if not tiles:
        return None
    for t in tiles:
        if t["lbl"] and keyword in t["lbl"]:
            return t
    return None


def theme_attr(page):
    return page.eval_on_selector("html", "el => el.getAttribute('data-theme')")


def shot(page, shots_dir, name):
    try:
        page.screenshot(path=os.path.join(shots_dir, f"{name}.png"))
    except Exception:
        pass


# ============================================================
# 各组测试用例
# ============================================================

def run_group_A(page, shots_dir, console_errors, page_errors):
    def a1():
        n_console = len(console_errors)
        n_page = len(page_errors)
        ok = (n_console == 0 and n_page == 0)
        R.record("A1", "页面加载无 console error / page error",
                 "PASS" if ok else "FAIL",
                 expected="console error=0, page error=0",
                 actual=f"console error={n_console}, page error={n_page}",
                 detail=("; ".join(str(m) for m in (console_errors + page_errors))[:800]
                          if not ok else None))
    safe_run("A1", "页面加载无 console/page error", a1)

    def a2():
        set_gran(page, "manu")
        page.wait_for_timeout(300)
        names = legend_names(page)
        series = get_echarts_series(page)
        shot(page, shots_dir, "A2_default_view")
        ok_legend = bool(names) and len(names) > 0
        ok_series = isinstance(series, list) and len(series) > 0
        ok = ok_legend and ok_series
        R.record("A2", "默认视图：粒度=厂商，图例有对象，图上有折线",
                 "PASS" if ok else "FAIL",
                 expected="图例对象数>0 且 图表折线数>0",
                 actual=f"图例对象数={len(names) if names else 0}, 折线数={len(series) if isinstance(series,list) else series}",
                 detail=None if ok else "图例列表或图表 series 为空/异常")
    safe_run("A2", "默认视图基础健康检查", a2)

    def a3():
        before_console = len(console_errors)
        before_page = len(page_errors)
        btn = q(page, "#themeBtn")
        if btn is None:
            R.record("A3", "主题切换到深色再切回，无报错", "FAIL",
                      detail="找不到 #themeBtn")
            return
        t0 = theme_attr(page)
        btn.click()
        page.wait_for_timeout(300)
        t1 = theme_attr(page)
        btn.click()
        page.wait_for_timeout(300)
        t2 = theme_attr(page)
        after_console = len(console_errors)
        after_page = len(page_errors)
        no_new_errors = (after_console == before_console and after_page == before_page)
        toggled = (t1 != t0)
        ok = no_new_errors and toggled
        R.record("A3", "主题切换到深色再切回，无报错", "PASS" if ok else "FAIL",
                 expected="切换时 data-theme 发生变化，且切换前后无新增 console/page error",
                 actual=f"theme: {t0} -> {t1} -> {t2}; 新增 console error={after_console-before_console}, page error={after_page-before_page}")
    safe_run("A3", "主题切换回归", a3)


def run_group_B(page, shots_dir, oracle):
    def b1():
        set_gran(page, "model")
        page.wait_for_timeout(300)
        opts = get_select_options(page, "#bodyTypeSelect")
        if not opts:
            R.record("B1", "bodyTypeSelect 第一项 value=-1 文本含“全部”", "FAIL",
                      detail="找不到 #bodyTypeSelect 或没有 option")
            return
        first = opts[0]
        ok = (first["value"] == "-1") and ("全部" in first["text"])
        R.record("B1", "bodyTypeSelect 第一项 value=-1 文本含“全部”", "PASS" if ok else "FAIL",
                 expected="value=-1, text 包含“全部”",
                 actual=f"value={first['value']!r}, text={first['text']!r}")
    safe_run("B1", "bodyTypeSelect 首项为全部车体类型", b1)

    def b2():
        opts = get_select_options(page, "#bodyTypeSelect")
        if not opts:
            R.record("B2", "全部车体类型为默认选中值", "FAIL", detail="找不到 #bodyTypeSelect")
            return
        cur_val = get_select_value(page, "#bodyTypeSelect")
        first = opts[0]
        ok = (cur_val == first["value"]) and first.get("selected", False) is not False and cur_val == "-1"
        R.record("B2", "全部车体类型为默认选中值", "PASS" if ok else "FAIL",
                 expected="当前 select.value == '-1'",
                 actual=f"select.value={cur_val!r}")
    safe_run("B2", "全部车体类型默认选中", b2)

    def b3():
        opts = get_select_options(page, "#bodyTypeSelect")
        if not opts:
            R.record("B3", "全部车体类型池子大小合理", "FAIL", detail="找不到 #bodyTypeSelect")
            return
        # 确保回到“全部”
        set_select(page, "#bodyTypeSelect", "-1")
        cnt_all_txt = inner_text_or_none(page, "#legendCount") or ""
        m = re.search(r"(\d+)", cnt_all_txt)
        cnt_all = int(m.group(1)) if m else None

        # 找一个具体的车体类型 option（非“全部”），优先 SUV
        concrete = None
        for o in opts:
            if o["value"] != "-1":
                if o["text"] == "SUV":
                    concrete = o
                    break
                if concrete is None:
                    concrete = o
        if concrete is None:
            R.record("B3", "全部车体类型池子大小合理", "FAIL", detail="下拉框里没有具体车体类型选项")
            return
        set_select(page, "#bodyTypeSelect", concrete["value"])
        cnt_one_txt = inner_text_or_none(page, "#legendCount") or ""
        m2 = re.search(r"(\d+)", cnt_one_txt)
        cnt_one = int(m2.group(1)) if m2 else None
        set_select(page, "#bodyTypeSelect", "-1")  # 恢复
        shot(page, shots_dir, "B3_all_body_types")

        # Python 侧口径参照：当年有销量的车型数（全部 / 该具体车体类型）
        year = current_year(page)
        bt_idx_map = {bt: i for i, bt in enumerate(oracle.raw["bodyTypes"])}
        concrete_idx = bt_idx_map.get(concrete["text"])
        expect_all, _, _ = oracle.models_with_sales_count(year, body_type_idx=None)
        expect_one, _, _ = oracle.models_with_sales_count(year, body_type_idx=concrete_idx)
        total_raw = len(oracle.raw["model"]["n"])

        ok = (cnt_all is not None and cnt_one is not None
              and cnt_all == expect_all
              and cnt_all > cnt_one)
        R.record("B3", "全部车体类型池子大小 == 当年有销量车型数，且 > 单一车体类型池子",
                 "PASS" if ok else "FAIL",
                 expected=f"全部池子={expect_all}（当年有销量车型数，RAW.model.n.length={total_raw} 仅供量级参照），"
                          f"且 > {concrete['text']} 池子({expect_one})",
                 actual=f"全部池子(#legendCount)={cnt_all}, {concrete['text']}池子={cnt_one}",
                 detail=None if ok else f"文本原文: 全部='{cnt_all_txt}', {concrete['text']}='{cnt_one_txt}'")
    safe_run("B3", "全部车体类型池子大小校验", b3)


def current_year(page):
    txt = inner_text_or_none(page, "#chartTitle") or ""
    m = re.search(r"(20\d\d)年", txt)
    if m:
        return int(m.group(1))
    active = q(page, '#yearChips .chip.active')
    if active is not None:
        y = active.get_attribute("data-year")
        if y:
            return int(y)
    return 2026


def run_group_C(page, shots_dir, oracle):
    MANU = "一汽红旗"
    BRAND = "红旗"

    def c1():
        set_gran(page, "model")
        page.wait_for_timeout(200)
        vis_model = is_visible(page, "#ownerGroup")
        set_gran(page, "manu")
        page.wait_for_timeout(200)
        vis_manu = is_visible(page, "#ownerGroup")
        set_gran(page, "brand")
        page.wait_for_timeout(200)
        vis_brand = is_visible(page, "#ownerGroup")
        set_gran(page, "model")
        page.wait_for_timeout(200)
        ok = vis_model and (not vis_manu) and (not vis_brand)
        R.record("C1", "ownerGroup 仅在 model 粒度可见", "PASS" if ok else "FAIL",
                 expected="model=visible, manu=hidden, brand=hidden",
                 actual=f"model={vis_model}, manu={vis_manu}, brand={vis_brand}")
    safe_run("C1", "ownerGroup 可见性随粒度切换", c1)

    def c2():
        set_gran(page, "model")
        page.wait_for_timeout(200)
        struct = get_optgroup_structure(page, "#ownerSelect")
        if not struct:
            R.record("C2", "ownerSelect 结构（all/optgroup/manu:一汽红旗）", "FAIL",
                      detail="找不到 #ownerSelect 或没有子节点")
            return
        first_is_all = (struct[0]["tag"] == "OPTION" and struct[0]["value"] == "all")
        cur_val = get_select_value(page, "#ownerSelect")
        default_ok = (cur_val == "all")
        has_optgroup = any(c["tag"] == "OPTGROUP" for c in struct)
        has_target = False
        for c in struct:
            if c["tag"] == "OPTGROUP":
                for o in c["options"]:
                    if o["value"] == f"manu:{MANU}":
                        has_target = True
        ok = first_is_all and default_ok and has_optgroup and has_target
        R.record("C2", "ownerSelect 含 all 默认项 / optgroup / manu:一汽红旗", "PASS" if ok else "FAIL",
                 expected="首项 value=all 且默认选中；存在 optgroup；存在 value='manu:一汽红旗' 的选项",
                 actual=f"首项={struct[0] if struct else None}, 当前值={cur_val}, has_optgroup={has_optgroup}, has_target={has_target}")
    safe_run("C2", "ownerSelect 结构校验", c2)

    def c3():
        set_gran(page, "model")
        set_select(page, "#bodyTypeSelect", "-1")
        el = q(page, "#ownerSelect")
        if el is None:
            R.record("C3", "归属=一汽红旗：图例全部属于一汽红旗，数量=20", "FAIL",
                      detail="找不到 #ownerSelect")
            return
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        page.wait_for_timeout(250)
        names = legend_names(page)
        shot(page, shots_dir, "C3_owner_hongqi")
        if names is None:
            R.record("C3", "归属=一汽红旗：图例全部属于一汽红旗，数量=20", "FAIL",
                      detail="图例列表读取失败")
            return
        year = current_year(page)
        owned_all = oracle.owned_model_name_set(manu_name=MANU)
        n_with_sales, names_with_sales, names_all_owned = oracle.models_with_sales_count(year, manu_name=MANU)
        not_owned = [n for n in names if n not in owned_all]
        ok_membership = (len(not_owned) == 0) and len(names) > 0
        ok_count = (len(names) == n_with_sales)
        ok = ok_membership and ok_count
        R.record("C3", "归属=一汽红旗：图例全部属于一汽红旗，数量=20（当年有销量车型数）",
                 "PASS" if ok else "FAIL",
                 expected=f"图例条目数={n_with_sales}（当年有销量车型），且每条都在一汽红旗名下（含未过滤零销量口径共 {len(owned_all)} 款作为归属集合参照）",
                 actual=f"图例条目数={len(names)}，其中不属于一汽红旗的={not_owned if not_owned else '无'}",
                 detail=(f"若按“不过滤零销量”的归属口径，一汽红旗总车型数={len(owned_all)}；"
                         f"当前图例名单={names}" if not ok else None))
    safe_run("C3", "归属筛选核心校验：一汽红旗", c3)

    def c4():
        # 独立设置前置条件（不依赖 C3 是否成功跑完/是否改动了 owner），
        # 避免 C3 提前抛异常时 C4 在“未真正筛选归属”的状态下被误判为 PASS。
        set_gran(page, "model")
        el = q(page, "#ownerSelect")
        if el is None:
            R.record("C4", "归属=一汽红旗：折线条数==已勾选数量（默认Top20=20）", "FAIL",
                      detail="找不到 #ownerSelect，无法建立归属筛选前置条件")
            return
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        page.wait_for_timeout(200)
        series = get_echarts_series(page)
        shown_txt = inner_text_or_none(page, "#legendShownCount") or ""
        m = re.search(r"(\d+)", shown_txt)
        shown_count = int(m.group(1)) if m else None
        if not isinstance(series, list):
            R.record("C4", "归属=一汽红旗：折线条数==已勾选数量（默认Top20=20）", "FAIL",
                      detail=f"echarts series 读取失败: {series}")
            return
        real_series = [s for s in series if s.get("id") != "__other__"]
        ok = (shown_count is not None and len(real_series) == shown_count == 20)
        R.record("C4", "归属=一汽红旗：折线条数==已勾选数量（默认Top20=20）",
                 "PASS" if ok else "FAIL",
                 expected="折线数(排除“其他”)==已选计数==20",
                 actual=f"折线数(排除其他)={len(real_series)}, 已选计数文本='{shown_txt}'")
    safe_run("C4", "归属筛选下折线数与勾选数一致", c4)

    def c5():
        el = q(page, "#ownerSelect")
        if el is None:
            R.record("C5", "归属=品牌:红旗，池子内容合理", "FAIL", detail="找不到 #ownerSelect")
            return
        set_select(page, "#ownerSelect", f"brand:{BRAND}")
        names = legend_names(page)
        if names is None:
            R.record("C5", "归属=品牌:红旗，池子内容合理", "FAIL", detail="图例列表读取失败")
            return
        year = current_year(page)
        owned_all = oracle.owned_model_name_set(brand_name=BRAND)
        n_with_sales, names_with_sales, _ = oracle.models_with_sales_count(year, brand_name=BRAND)
        not_owned = [n for n in names if n not in owned_all]
        ok = (len(not_owned) == 0) and (len(names) == n_with_sales) and len(names) > 0
        R.record("C5", "归属=品牌:红旗，池子内容合理（品牌口径车型集合）", "PASS" if ok else "FAIL",
                 expected=f"图例条目数={n_with_sales}，且每条都在品牌“红旗”名下",
                 actual=f"图例条目数={len(names)}，不属于红旗品牌的={not_owned if not_owned else '无'}")
    safe_run("C5", "归属=品牌口径校验", c5)

    def c6():
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        set_energy(page, "ev")
        page.wait_for_timeout(250)
        names = legend_names(page)
        if names is None:
            R.record("C6", "归属+能源(新能源)叠加：池子为交集，不报错", "FAIL", detail="图例列表读取失败")
            try:
                set_energy(page, "all")
            except Exception:
                pass
            return
        year = current_year(page)
        owned_all = oracle.owned_model_name_set(manu_name=MANU)
        n_expect, names_expect, _ = oracle.models_with_sales_count(year, manu_name=MANU, energy="ev")
        not_owned = [n for n in names if n not in owned_all]
        ok = (len(not_owned) == 0) and (len(names) == n_expect)
        R.record("C6", "归属+能源(新能源)叠加：池子为交集，不报错", "PASS" if ok else "FAIL",
                 expected=f"图例条目数={n_expect}（一汽红旗 且 新能源 ytd>0 的交集），全部属于一汽红旗",
                 actual=f"图例条目数={len(names)}，不属于一汽红旗的={not_owned if not_owned else '无'}")
        try:
            set_energy(page, "all")
        except Exception:
            pass
    safe_run("C6", "归属+能源筛选交集校验", c6)

    def c7():
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        page.wait_for_timeout(200)
        reset_btn = q(page, "#resetBtn")
        clear_btn = q(page, "#clearBtn")
        if reset_btn is None or clear_btn is None:
            R.record("C7", "归属筛选下 resetBtn/clearBtn 行为正常", "FAIL",
                      detail="找不到 #resetBtn 或 #clearBtn")
            return
        reset_btn.click()
        page.wait_for_timeout(200)
        shown1 = re.search(r"(\d+)", inner_text_or_none(page, "#legendShownCount") or "")
        shown1 = int(shown1.group(1)) if shown1 else None
        clear_btn.click()
        page.wait_for_timeout(200)
        shown2 = re.search(r"(\d+)", inner_text_or_none(page, "#legendShownCount") or "")
        shown2 = int(shown2.group(1)) if shown2 else None
        reset_btn.click()
        page.wait_for_timeout(200)
        shown3 = re.search(r"(\d+)", inner_text_or_none(page, "#legendShownCount") or "")
        shown3 = int(shown3.group(1)) if shown3 else None
        ok = (shown1 is not None and shown1 > 0 and shown2 == 0 and shown3 == shown1)
        R.record("C7", "归属筛选下 resetBtn/clearBtn 行为正常", "PASS" if ok else "FAIL",
                 expected="reset后已选>0；clear后已选==0；再次reset恢复到与第一次一致",
                 actual=f"reset1={shown1}, clear={shown2}, reset2={shown3}")
    safe_run("C7", "归属筛选下 reset/clear 行为", c7)


def run_group_D(page, shots_dir, oracle):
    MANU = "一汽红旗"

    def d1():
        set_gran(page, "model")
        el = q(page, "#ownerSelect")
        if el is None:
            R.record("D1", "归属≠全部时 chartTitle 含厂商/品牌名", "FAIL", detail="找不到 #ownerSelect")
            return
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        title = inner_text_or_none(page, "#chartTitle") or ""
        shot(page, shots_dir, "D1_title_owner")
        ok = MANU in title
        R.record("D1", "归属≠全部时 chartTitle 含厂商/品牌名", "PASS" if ok else "FAIL",
                 expected=f"chartTitle 包含 '{MANU}'",
                 actual=f"chartTitle='{title}'")
    safe_run("D1", "口径标注：归属体现在标题里", d1)

    def d2():
        set_gran(page, "model")
        set_select_maybe(page, "#ownerSelect", "all")  # 非本用例主体，尽量恢复但不强制要求存在
        set_select(page, "#bodyTypeSelect", "-1")
        title = inner_text_or_none(page, "#chartTitle") or ""
        specific_types = [t for t in oracle.raw["bodyTypes"]]
        # 标题必须能体现“全部车体类型”，且不能被单独渲染成某个具体类型（如仅 "SUV" 而不提全部）
        mentions_all = ("全部车体类型" in title) or ("全部" in title and "车体类型" in title) or ("全部车体类型" in title)
        looks_like_single_type_only = any(
            (t in title and "全部" not in title) for t in specific_types
        )
        ok = mentions_all and not looks_like_single_type_only
        R.record("D2", "车体类型=全部时 chartTitle 表明“全部车体类型”", "PASS" if ok else "FAIL",
                 expected="标题包含“全部车体类型”，且不应显示成具体车体类型名而不提全部",
                 actual=f"chartTitle='{title}'")
    safe_run("D2", "口径标注：全部车体类型体现在标题里", d2)

    def d3():
        set_gran(page, "model")
        el = q(page, "#ownerSelect")
        if el is None:
            R.record("D3", "归属筛选下抽屉“排名”tile 标签含比较池信息", "FAIL", detail="找不到 #ownerSelect")
            return
        set_select(page, "#ownerSelect", f"manu:{MANU}")
        page.wait_for_timeout(200)
        names = legend_names(page) or []
        if not names:
            R.record("D3", "归属筛选下抽屉“排名”tile 标签含比较池信息", "FAIL",
                      detail="图例为空，无法打开抽屉")
            return
        opened = open_drawer_by_name(page, names[0])
        if not opened:
            R.record("D3", "归属筛选下抽屉“排名”tile 标签含比较池信息", "FAIL",
                      detail=f"无法打开 '{names[0]}' 的抽屉")
            return
        tiles = get_drawer_stat_tiles(page)
        shot(page, shots_dir, "D3_drawer_rank_scoped")
        close_drawer(page)
        tile = find_tile(tiles, "排名")
        if tile is None:
            R.record("D3", "归属筛选下抽屉“排名”tile 标签含比较池信息", "FAIL",
                      detail=f"drawerStats 里没有含“排名”的 tile。全部 tiles={tiles}")
            return
        lbl = tile["lbl"] or ""
        ok = (lbl.strip() != "当前排名") and (MANU in lbl or any(
            kw in lbl for kw in ["范围", "口径", "筛选", "归属", "内"]
        ))
        R.record("D3", "归属筛选下抽屉“排名”tile 标签含比较池信息", "PASS" if ok else "FAIL",
                 expected="标签不能是光秃秃的“当前排名”，需体现比较池（如含厂商名或“范围/口径/内”等字样）",
                 actual=f"排名 tile 的 .lbl = '{lbl}'")
    safe_run("D3", "口径标注：归属筛选下排名标签", d3)

    def d4():
        set_gran(page, "model")
        set_select_maybe(page, "#ownerSelect", "all")  # 非本用例主体，尽量恢复但不强制要求存在
        set_select(page, "#bodyTypeSelect", "-1")
        page.wait_for_timeout(200)
        names = legend_names(page) or []
        if not names:
            R.record("D4", "无筛选时排名 tile 标签保持简洁形式", "FAIL", detail="图例为空")
            return
        opened = open_drawer_by_name(page, names[0])
        if not opened:
            R.record("D4", "无筛选时排名 tile 标签保持简洁形式", "FAIL",
                      detail=f"无法打开 '{names[0]}' 的抽屉")
            return
        tiles = get_drawer_stat_tiles(page)
        close_drawer(page)
        tile = find_tile(tiles, "排名")
        if tile is None:
            R.record("D4", "无筛选时排名 tile 标签保持简洁形式", "FAIL",
                      detail=f"drawerStats 里没有含“排名”的 tile。全部 tiles={tiles}")
            return
        lbl = (tile["lbl"] or "").strip()
        ok = (lbl == "当前排名")
        R.record("D4", "无筛选时排名 tile 标签保持简洁形式", "PASS" if ok else "FAIL",
                 expected="标签就是“当前排名”（无额外比较池括注）",
                 actual=f"排名 tile 的 .lbl = '{lbl}'")
    safe_run("D4", "口径标注：无筛选时排名标签简洁", d4)

    def d5():
        set_gran(page, "model")
        el = q(page, "#otherToggle")
        if el is None:
            R.record("D5", "开启“其他”聚合线，筛选下线名体现范围", "FAIL", detail="找不到 #otherToggle")
            return
        # 用一个具体车体类型（而非归属）筛选，池子远大于默认 Top20（如 SUV 通常 400+ 款车型），
        # 保证勾选之外一定有未展示对象、"其他"线一定会出现，不依赖某个归属筛选池子刚好等于/小于20。
        set_select_maybe(page, "#ownerSelect", "all")
        opts = get_select_options(page, "#bodyTypeSelect") or []
        concrete = next((o for o in opts if o["value"] != "-1"), None)
        bt_text = concrete["text"] if concrete else None
        if concrete:
            set_select(page, "#bodyTypeSelect", concrete["value"])
        if not el.is_checked():
            el.click()
        page.wait_for_timeout(300)
        series = get_echarts_series(page)
        if not isinstance(series, list):
            R.record("D5", "开启“其他”聚合线，筛选下线名体现范围", "FAIL",
                      detail=f"echarts series 读取失败: {series}")
            set_select_maybe(page, "#bodyTypeSelect", "-1")
            return
        other = next((s for s in series if s.get("id") == "__other__"), None)
        if other is None:
            R.record("D5", "开启“其他”聚合线，筛选下线名体现范围", "N/A",
                      detail=f"车体类型={bt_text} 筛选下仍未出现“其他”线（可能该类型池子<=20，或勾选覆盖了全部对象），跳过判定")
            if el.is_checked():
                el.click()
            set_select_maybe(page, "#bodyTypeSelect", "-1")
            page.wait_for_timeout(200)
            return
        name = other.get("name") or ""
        ok = (name.strip() != "其他") and (bt_text in name or any(kw in name for kw in ["范围", "内", "口径"]))
        R.record("D5", "开启“其他”聚合线，筛选下线名体现范围", "PASS" if ok else "FAIL",
                 expected=f"“其他”聚合线的 series.name 不能是光秃秃的“其他”，需体现当前筛选范围（如含“{bt_text}”）",
                 actual=f"series.name='{name}'")
        # 收尾恢复：best-effort，不能因为恢复失败而覆盖掉上面已经记录的真实结果
        if el.is_checked():
            el.click()
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        page.wait_for_timeout(200)
    safe_run("D5", "口径标注：其他聚合线体现范围", d5)


def run_group_E(page, shots_dir, oracle):
    MANU = "一汽红旗"
    BRAND = "红旗"

    def e1():
        set_gran(page, "manu")
        page.wait_for_timeout(150)
        opened = open_drawer_by_name(page, MANU)
        if not opened:
            R.record("E1", "厂商粒度抽屉里 drillDownBtn 存在且可见", "FAIL",
                      detail=f"打不开 '{MANU}' 的抽屉（可能不在当前图例里，检查搜索框/池子）")
            return
        btn = q(page, "#drillDownBtn")
        shot(page, shots_dir, "E1_drawer_manu_drilldown")
        ok = (btn is not None) and btn.is_visible()
        R.record("E1", "厂商粒度抽屉里 drillDownBtn 存在且可见", "PASS" if ok else "FAIL",
                 expected="#drillDownBtn 存在且可见",
                 actual=f"存在={btn is not None}, 可见={btn.is_visible() if btn else None}")
        close_drawer(page)
    safe_run("E1", "厂商粒度下钻按钮存在", e1)

    def e2():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        page.wait_for_timeout(150)
        names = legend_names(page) or []
        if not names:
            R.record("E2", "车型粒度抽屉里 drillDownBtn 不应存在", "FAIL", detail="图例为空")
            return
        opened = open_drawer_by_name(page, names[0])
        if not opened:
            R.record("E2", "车型粒度抽屉里 drillDownBtn 不应存在", "FAIL",
                      detail=f"打不开 '{names[0]}' 的抽屉")
            return
        btn = q(page, "#drillDownBtn")
        shot(page, shots_dir, "E2_drawer_model_no_drilldown")
        ok = (btn is None) or (not btn.is_visible())
        R.record("E2", "车型粒度抽屉里 drillDownBtn 不应存在", "PASS" if ok else "FAIL",
                 expected="#drillDownBtn 不存在或不可见",
                 actual=f"存在={btn is not None}, 可见={btn.is_visible() if btn else None}")
        close_drawer(page)
    safe_run("E2", "车型粒度无下钻按钮", e2)

    def e3():
        set_gran(page, "manu")
        opened = open_drawer_by_name(page, MANU)
        if not opened:
            R.record("E3", "点击下钻：抽屉关闭/粒度变model/车体类型全部/归属=manu:一汽红旗/图上出现红旗车型",
                      "FAIL", detail=f"打不开 '{MANU}' 的抽屉")
            return
        btn = q(page, "#drillDownBtn")
        if btn is None:
            R.record("E3", "点击下钻：抽屉关闭/粒度变model/车体类型全部/归属=manu:一汽红旗/图上出现红旗车型",
                      "FAIL", detail="找不到 #drillDownBtn，无法点击")
            close_drawer(page)
            return
        btn.click()
        page.wait_for_timeout(400)
        drawer_open = drawer_is_open(page)
        gran_active = q(page, '#granChips .chip[data-gran="model"].active') is not None
        bt_val = get_select_value(page, "#bodyTypeSelect")
        owner_val = get_select_value(page, "#ownerSelect")
        names = legend_names(page) or []
        owned_all = oracle.owned_model_name_set(manu_name=MANU)
        not_owned = [n for n in names if n not in owned_all]
        shot(page, shots_dir, "E3_after_drilldown")
        ok = ((not drawer_open) and gran_active and bt_val == "-1" and owner_val == f"manu:{MANU}"
              and len(names) > 0 and not not_owned)
        R.record("E3", "点击下钻：抽屉关闭/粒度变model/车体类型全部/归属=manu:一汽红旗/图上出现红旗车型",
                 "PASS" if ok else "FAIL",
                 expected="drawer关闭, gran=model, bodyType=-1, owner=manu:一汽红旗, 图例全部属于一汽红旗",
                 actual=f"drawer可见={drawer_open}, gran=model激活={gran_active}, bodyType值={bt_val}, "
                        f"owner值={owner_val}, 图例数={len(names)}, 非红旗条目={not_owned}")
    safe_run("E3", "点击下钻后状态与图表校验", e3)

    def e4():
        set_gran(page, "brand")
        opened = open_drawer_by_name(page, BRAND)
        if not opened:
            R.record("E4", "品牌粒度下钻按钮存在且行为正确", "FAIL",
                      detail=f"打不开品牌 '{BRAND}' 的抽屉")
            return
        btn = q(page, "#drillDownBtn")
        if btn is None or not btn.is_visible():
            R.record("E4", "品牌粒度下钻按钮存在且行为正确", "FAIL",
                      detail=f"drillDownBtn 不存在或不可见 (存在={btn is not None})")
            close_drawer(page)
            return
        btn.click()
        page.wait_for_timeout(400)
        gran_active = q(page, '#granChips .chip[data-gran="model"].active') is not None
        owner_val = get_select_value(page, "#ownerSelect")
        names = legend_names(page) or []
        owned_all = oracle.owned_model_name_set(brand_name=BRAND)
        not_owned = [n for n in names if n not in owned_all]
        ok = gran_active and owner_val == f"brand:{BRAND}" and len(names) > 0 and not not_owned
        R.record("E4", "品牌粒度下钻按钮存在且行为正确", "PASS" if ok else "FAIL",
                 expected=f"gran=model, owner=brand:{BRAND}, 图例全部属于该品牌",
                 actual=f"gran=model激活={gran_active}, owner值={owner_val}, 图例数={len(names)}, 非该品牌条目={not_owned}")
    safe_run("E4", "品牌粒度下钻校验", e4)


def set_select_maybe(page, sel, value):
    el = q(page, sel)
    if el is not None:
        try:
            opts = get_select_options(page, sel) or []
            if any(o["value"] == value for o in opts):
                page.select_option(sel, value, timeout=3000)
                page.wait_for_timeout(200)
        except Exception:
            pass


def run_group_F(page, shots_dir):
    def f1():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        page.wait_for_timeout(200)
        clear_btn = q(page, "#clearBtn")
        bt_select = q(page, "#bodyTypeSelect")
        if clear_btn is None or bt_select is None:
            R.record("F1", "车型粒度：切换车体类型再切回“全部”，勾选状态不丢失/不重复", "FAIL",
                      detail="找不到 #clearBtn 或 #bodyTypeSelect")
            return
        clear_btn.click()
        page.wait_for_timeout(200)
        names = legend_names(page) or []
        if len(names) < 2:
            R.record("F1", "车型粒度：切换车体类型再切回“全部”，勾选状态不丢失/不重复", "FAIL",
                      detail=f"图例对象不足2个，无法测试（当前{len(names)}个）")
            return
        pick = names[:2]
        for nm in pick:
            click_legend_checkbox_by_name(page, nm)
        checked_before = sorted(legend_checked_names(page) or [])
        opts = get_select_options(page, "#bodyTypeSelect")
        concrete_val = next((o["value"] for o in opts if o["value"] != "-1"), None)
        if concrete_val is not None:
            set_select(page, "#bodyTypeSelect", concrete_val)
        set_select(page, "#bodyTypeSelect", "-1")
        checked_after = legend_checked_names(page) or []
        shot(page, shots_dir, "F1_roundtrip_bodytype")
        dup = len(checked_after) != len(set(checked_after))
        checked_after_sorted = sorted(checked_after)
        ok = (checked_after_sorted == sorted(pick)) and not dup
        R.record("F1", "车型粒度：切换车体类型再切回“全部”，勾选状态不丢失/不重复",
                 "PASS" if ok else "FAIL",
                 expected=f"往返后勾选集合仍为 {sorted(pick)}，且图例无重复条目",
                 actual=f"往返前勾选={checked_before}, 往返后勾选={checked_after_sorted}, 图例内有重复={dup}")
    safe_run("F1", "车型粒度勾选状态在车体类型往返切换后的一致性", f1)

    def f2():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        page.wait_for_timeout(150)
        names = legend_names(page) or []
        if len(names) < 1:
            R.record("F2", "车型↔厂商粒度往返后不报错、图例无重复条目", "FAIL", detail="图例为空")
            return
        try:
            click_legend_checkbox_by_name(page, names[0])
        except Exception:
            pass
        set_gran(page, "manu")
        page.wait_for_timeout(200)
        set_gran(page, "model")
        page.wait_for_timeout(200)
        names_after = legend_names(page) or []
        dup = len(names_after) != len(set(names_after))
        shot(page, shots_dir, "F2_roundtrip_gran")
        ok = (not dup) and len(names_after) > 0
        R.record("F2", "车型↔厂商粒度往返后不报错、图例无重复条目", "PASS" if ok else "FAIL",
                 expected="往返后图例非空且无重复名称",
                 actual=f"图例条目数={len(names_after)}, 存在重复={dup}")
    safe_run("F2", "车型/厂商粒度往返一致性", f2)

    def f3():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        page.wait_for_timeout(150)
        names = legend_names(page) or []
        dup_names = [n for n in set(names) if names.count(n) > 1]
        ok = len(dup_names) == 0 and len(names) > 0
        R.record("F3", "图例里不存在同名重复项", "PASS" if ok else "FAIL",
                 expected="图例名称去重后数量与原数量一致",
                 actual=f"图例总数={len(names)}, 去重后={len(set(names))}, 重复项={dup_names[:10]}")
    safe_run("F3", "图例名称唯一性", f3)


def run_group_G(page, shots_dir, oracle):
    def g1():
        set_gran(page, "manu")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        opened = open_drawer_by_name(page, "比亚迪")
        if not opened:
            R.record("G1", "比亚迪2026年同比回归哨兵 == -43.8%", "FAIL",
                      detail="打不开'比亚迪'的抽屉（检查是否在当前图例/年份里）")
            return
        tiles = get_drawer_stat_tiles(page)
        shot(page, shots_dir, "G1_byd_yoy")
        close_drawer(page)
        tile = find_tile(tiles, "同比")
        if tile is None:
            R.record("G1", "比亚迪2026年同比回归哨兵 == -43.8%", "FAIL",
                      detail=f"drawerStats 里没有含“同比”的 tile。全部 tiles={tiles}")
            return
        val = (tile["val"] or "").strip()
        expected = oracle.yoy_pct("manu", "比亚迪", 2026)
        m = re.search(r"-?\d+(\.\d+)?", val)
        actual_num = float(m.group(0)) if m else None
        ok = (actual_num is not None and expected is not None and abs(actual_num - expected) < 0.05)
        R.record("G1", "比亚迪2026年同比回归哨兵 == -43.8%", "PASS" if ok else "FAIL",
                 expected=f"{expected}%（独立复刻口径计算得出）",
                 actual=f"抽屉显示值='{val}'")
    safe_run("G1", "回归哨兵：比亚迪同比口径", g1)

    def g2():
        set_gran(page, "manu")
        btn = q(page, "#tableToggleBtn")
        if btn is None:
            R.record("G2", "表格视图可切换、能渲染出行", "FAIL", detail="找不到 #tableToggleBtn")
            return
        btn.click()
        page.wait_for_timeout(300)
        rows = qa(page, "#tableview table tbody tr") or qa(page, "#tableview tr")
        n_rows = len(rows) if rows else 0
        shot(page, shots_dir, "G2_table_view")
        btn.click()
        page.wait_for_timeout(200)
        ok = n_rows > 0
        R.record("G2", "表格视图可切换、能渲染出行", "PASS" if ok else "FAIL",
                 expected="切到表格视图后 tbody 行数 > 0",
                 actual=f"行数={n_rows}")
    safe_run("G2", "表格视图渲染", g2)

    def g3():
        set_gran(page, "manu")
        el = q(page, "#ownerSelect")
        if el is not None:
            set_select_maybe(page, "#ownerSelect", "all")
        set_energy(page, "ev")
        page.wait_for_timeout(200)
        btn = q(page, "#tableToggleBtn")
        dl_btn = q(page, "#downloadCsvBtn")
        if btn is None or dl_btn is None:
            R.record("G3", "下载CSV按钮在筛选状态下可点击并触发下载", "FAIL",
                      detail="找不到 #tableToggleBtn 或 #downloadCsvBtn")
            set_energy(page, "all")
            return
        btn.click()
        page.wait_for_timeout(300)
        try:
            with page.expect_download(timeout=5000) as dl_info:
                dl_btn.click()
            download = dl_info.value
            path = download.path()
            size = os.path.getsize(path) if path else 0
            ok = size > 0
            R.record("G3", "下载CSV按钮在筛选状态下可点击并触发下载", "PASS" if ok else "FAIL",
                     expected="触发下载且文件非空",
                     actual=f"下载文件大小={size} bytes, 文件名={download.suggested_filename}")
        except PWTimeout:
            R.record("G3", "下载CSV按钮在筛选状态下可点击并触发下载", "FAIL",
                      detail="点击后 5s 内未捕获到下载事件")
        except Exception as e:
            R.record("G3", "下载CSV按钮在筛选状态下可点击并触发下载", "FAIL",
                      detail=f"异常: {e}")
        finally:
            # best-effort 收尾，绝不能因为收尾失败而给已记录的结果追加一条重复记录
            try:
                btn.click()
                page.wait_for_timeout(150)
                set_energy(page, "all")
            except Exception:
                pass
    safe_run("G3", "下载CSV回归", g3)

    def g4():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        el = q(page, "#ownerSelect")
        if el is not None:
            set_select_maybe(page, "#ownerSelect", "manu:一汽红旗")
        theme_btn = q(page, "#themeBtn")
        if theme_btn is not None and theme_attr(page) != "dark":
            theme_btn.click()
            page.wait_for_timeout(300)
        shot(page, shots_dir, "G4_dark_mode_owner_filter")
        R.record("G4", "深色模式下 C3 场景界面截图", "PASS",
                 detail=f"截图已保存到 {shots_dir}/G4_dark_mode_owner_filter.png")
        if theme_btn is not None and theme_attr(page) == "dark":
            theme_btn.click()
            page.wait_for_timeout(200)
    safe_run("G4", "深色模式截图", g4)



# ============================================================
# H 组 · 零销量过滤（新，回归修正1）
# ============================================================

def run_group_H(page, shots_dir, oracle):
    def h1():
        set_gran(page, "manu")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        cnt_txt = inner_text_or_none(page, "#legendCount") or ""
        m = re.search(r"(\d+)", cnt_txt)
        cnt = int(m.group(1)) if m else None
        expect, _ = oracle.pool_count_with_sales("manu", 2026)
        ok = (cnt == expect)
        R.record("H1", "厂商粒度 2026 年池子应为99（全库117）", "PASS" if ok else "FAIL",
                 expected=f"legendCount 数字={expect}",
                 actual=f"legendCount文本='{cnt_txt}' 解析出={cnt}")
    safe_run("H1", "零销量过滤：厂商粒度池子大小", h1)

    def h2():
        set_gran(page, "brand")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        cnt_txt = inner_text_or_none(page, "#legendCount") or ""
        m = re.search(r"(\d+)", cnt_txt)
        cnt = int(m.group(1)) if m else None
        expect, _ = oracle.pool_count_with_sales("brand", 2026)
        ok = (cnt == expect)
        R.record("H2", "品牌粒度 2026 年池子应为104（全库121）", "PASS" if ok else "FAIL",
                 expected=f"legendCount 数字={expect}",
                 actual=f"legendCount文本='{cnt_txt}' 解析出={cnt}")
    safe_run("H2", "零销量过滤：品牌粒度池子大小", h2)

    def h3():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        cnt_txt = inner_text_or_none(page, "#legendCount") or ""
        m = re.search(r"(\d+)", cnt_txt)
        cnt = int(m.group(1)) if m else None
        expect, _, _ = oracle.models_with_sales_count(2026, body_type_idx=None)
        ok = (cnt == expect)
        R.record("H3", "车型粒度(车体类型=全部/归属=全部) 2026年池子应为721（全库895）",
                 "PASS" if ok else "FAIL",
                 expected=f"legendCount 数字={expect}",
                 actual=f"legendCount文本='{cnt_txt}' 解析出={cnt}")
    safe_run("H3", "零销量过滤：车型粒度池子大小", h3)

    def h4():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        names = legend_names(page) or []
        if not names:
            R.record("H4", "图例里不应出现累计销量为0的对象", "FAIL", detail="图例为空，无法测试")
            return
        zero_names = []
        missing_names = []
        for nm in names:
            y = oracle.model_ytd_by_name(nm, 2026)
            if y is None:
                missing_names.append(nm)
            elif y <= 0:
                zero_names.append(nm)
        ok = (not zero_names) and (not missing_names)
        R.record("H4", "图例里不应出现累计销量为0的对象（逐项用YTD口径核验）",
                 "PASS" if ok else "FAIL",
                 expected="所有图例名字在 RAW 里独立复刻的 YTD > 0",
                 actual=f"图例总数={len(names)}，0销量条目={zero_names[:10]}，"
                        f"在RAW里找不到名字的={missing_names[:10]}")
    safe_run("H4", "零销量过滤：逐项校验", h4)

    def h5():
        results = {}
        for gran, kind in [("manu", "manu"), ("brand", "brand"), ("model", "model")]:
            set_gran(page, gran)
            if gran == "model":
                set_select_maybe(page, "#bodyTypeSelect", "-1")
                set_select_maybe(page, "#ownerSelect", "all")
            set_year(page, 2025)
            page.wait_for_timeout(200)
            cnt_txt = inner_text_or_none(page, "#legendCount") or ""
            m = re.search(r"(\d+)", cnt_txt)
            cnt = int(m.group(1)) if m else None
            if kind == "model":
                expect, _, _ = oracle.models_with_sales_count(2025, body_type_idx=None)
            else:
                expect, _ = oracle.pool_count_with_sales(kind, 2025)
            results[gran] = (cnt, expect)
        set_year(page, 2026)  # 恢复，避免影响后续用例
        ok = all(cnt == expect for cnt, expect in results.values())
        R.record("H5", "2025年池子应各自变化（厂商109/品牌114/车型758），证明过滤逐年独立判断",
                 "PASS" if ok else "FAIL",
                 expected={k: v[1] for k, v in results.items()},
                 actual={k: v[0] for k, v in results.items()})
    safe_run("H5", "零销量过滤：跨年份独立校验", h5)

    def h6():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        ranks_txt = legend_ranks(page) or []
        nums, bad = [], []
        for t in ranks_txt:
            mm = re.match(r"#(\d+)", t or "")
            if mm:
                nums.append(int(mm.group(1)))
            else:
                bad.append(t)
        expect_seq = list(range(1, len(nums) + 1))
        ok = (not bad) and (nums == expect_seq)
        R.record("H6", "图例排名应从1开始连续，不因过滤留下空洞", "PASS" if ok else "FAIL",
                 expected=f"1..{len(nums)} 连续无跳号",
                 actual=f"前10个={nums[:10]}，末5个={nums[-5:] if len(nums) > 5 else nums}，"
                        f"无法解析的文本={bad[:5]}")
    safe_run("H6", "零销量过滤：排名连续性", h6)

    def h7():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        page.wait_for_timeout(200)
        target = "零跑A10"  # 2026年有销量（RAW复刻算出>0），2025全年销量=0（独立复刻验证过）
        opened = open_drawer_by_exact_name(page, target)
        if not opened:
            R.record("H7", f"“{target}”同比不产生NaN/Infinity/荒谬名次跳跃", "FAIL",
                      detail=f"打不开 '{target}' 的抽屉（检查该车型是否仍在2026年池子里）")
            return
        tiles = get_drawer_stat_tiles(page)
        shot(page, shots_dir, "H7_new_model_yoy")
        close_drawer(page)
        rank_tile = find_tile(tiles, "排名")
        yoy_tile = find_tile(tiles, "同比")
        if yoy_tile is None:
            R.record("H7", f"“{target}”同比不产生NaN/Infinity/荒谬名次跳跃", "FAIL",
                      detail=f"drawerStats 里没有含“同比”的 tile。全部 tiles={tiles}")
            return
        val_yoy = (yoy_tile["val"] or "")
        bad_yoy = bool(re.search(r"nan|infinity", val_yoy, re.I))
        ok_wording = ("新增" in val_yoy) or (val_yoy.strip() == "—")
        delta_rank = (rank_tile["delta"] or "") if rank_tile else ""
        bad_jump = bool(re.search(r"(上升|下降)\s*\d{3,}\s*名", delta_rank))
        ok = (not bad_yoy) and ok_wording and (not bad_jump)
        R.record("H7", f"“{target}”(2026有销量/2025全年零销量)同比不产生NaN/Infinity/荒谬名次跳跃",
                 "PASS" if ok else "FAIL",
                 expected="同比 tile 显示“新增”或“—”，不含 NaN/Infinity；排名同比不应出现三位数以上的离谱跳跃",
                 actual=f"同比tile.val='{val_yoy}'，排名tile.delta='{delta_rank}'")
    safe_run("H7", "零销量过滤引入的边界哨兵（新增对象同比）", h7)


# ============================================================
# I 组 · 计数措辞自解释（新）
# ============================================================

def run_group_I(page, shots_dir, oracle):
    def i1():
        set_gran(page, "manu")
        set_year(page, 2026)
        page.wait_for_timeout(150)
        txt = inner_text_or_none(page, "#metaCounts") or ""
        shot(page, shots_dir, "I1_header_counts")
        ok = ("累计" in txt) or ("收录" in txt)
        R.record("I1", "header覆盖措辞须表明“累计收录/全部年份”口径，避免跟图例数字打架",
                 "PASS" if ok else "FAIL",
                 expected="#metaCounts 文本包含“累计”或“收录”字样",
                 actual=f"#metaCounts='{txt}'")
    safe_run("I1", "口径自解释：header 措辞", i1)

    def i2():
        set_gran(page, "manu")
        set_year(page, 2026)
        page.wait_for_timeout(150)
        txt = inner_text_or_none(page, "#legendCount") or ""
        ok = ("2026" in txt) and ("有销量" in txt)
        R.record("I2", "图例计数措辞须含当前年份+“有销量”", "PASS" if ok else "FAIL",
                 expected="#legendCount 文本包含 '2026' 且包含 '有销量'",
                 actual=f"#legendCount='{txt}'")
    safe_run("I2", "口径自解释：图例计数措辞", i2)


# ============================================================
# J 组 · 非破坏性勾选（新，改动2的回归重点，这组最重要）
# ============================================================

def run_group_J(page, shots_dir, oracle):
    MODEL_A = "红旗H5"    # 轿车
    MODEL_B = "红旗HS5"   # SUV

    def _series_real(series):
        if not isinstance(series, list):
            return None
        return [s for s in series if s.get("id") != "__other__"]

    def j1():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        clear_btn = q(page, "#clearBtn")
        if clear_btn is None:
            R.record("J1", "车型/全部车体类型/全部归属：清除勾选后图上0条线", "FAIL",
                      detail="找不到 #clearBtn")
            return
        clear_btn.click()
        page.wait_for_timeout(250)
        series = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "J1_cleared")
        ok = (series is not None and len(series) == 0)
        R.record("J1", "车型/全部车体类型/全部归属：清除勾选后图上0条线", "PASS" if ok else "FAIL",
                 expected="折线数(排除“其他”)==0",
                 actual=f"折线数={len(series) if series is not None else series}")
    safe_run("J1", "非破坏性勾选：J1清空基线", j1)

    def j2():
        try:
            click_legend_checkbox_by_exact_name(page, MODEL_A)
            click_legend_checkbox_by_exact_name(page, MODEL_B)
        except AssertionError as e:
            R.record("J2", f"手动勾选“{MODEL_A}”“{MODEL_B}”：图上恰好2条线", "FAIL", detail=str(e))
            return
        page.wait_for_timeout(200)
        series = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "J2_two_checked")
        names = sorted(s.get("name") for s in series) if series is not None else None
        ok = (series is not None and len(series) == 2 and names == sorted([MODEL_A, MODEL_B]))
        R.record("J2", f"手动勾选“{MODEL_A}”“{MODEL_B}”：图上恰好2条线，名字对得上",
                 "PASS" if ok else "FAIL",
                 expected=f"折线数=2，名字={sorted([MODEL_A, MODEL_B])}",
                 actual=f"折线数={len(series) if series is not None else series}，名字={names}")
    safe_run("J2", "非破坏性勾选：J2手动勾选2个车型", j2)

    def j3():
        opts = get_select_options(page, "#bodyTypeSelect") or []
        suv = next((o for o in opts if o["text"] == "SUV"), None)
        if suv is None:
            R.record("J3", f"切到SUV：图上恰好1条线({MODEL_B})", "FAIL", detail="下拉框里没有SUV选项")
            return
        set_select(page, "#bodyTypeSelect", suv["value"])
        series = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "J3_suv_only")
        names = [s.get("name") for s in series] if series is not None else None
        ok = (series is not None and len(series) == 1 and names == [MODEL_B])
        R.record("J3", f"切到车体类型SUV：图上恰好1条线({MODEL_B})，{MODEL_A}(轿车)不在池子里退出画面",
                 "PASS" if ok else "FAIL",
                 expected=f"折线数=1，名字=['{MODEL_B}']",
                 actual=f"折线数={len(series) if series is not None else series}，名字={names}")
    safe_run("J3", "非破坏性勾选：J3切到SUV车体类型", j3)

    def j4():
        set_select(page, "#bodyTypeSelect", "-1")
        series = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "J4_back_to_all")
        names = sorted(s.get("name") for s in series) if series is not None else None
        ok = (series is not None and len(series) == 2 and names == sorted([MODEL_A, MODEL_B]))
        R.record("J4",
                 f"【核心】切回全部车体类型：图上恰好2条线，{MODEL_A}和{MODEL_B}都回来了（不多不少）",
                 "PASS" if ok else "FAIL",
                 expected=f"折线数=2，名字={sorted([MODEL_A, MODEL_B])}",
                 actual=f"折线数={len(series) if series is not None else series}，名字={names}")
    safe_run("J4", "非破坏性勾选：J4核心-车体类型往返后勾选完整恢复", j4)

    def j5():
        set_gran(page, "manu")
        page.wait_for_timeout(200)
        checked = legend_checked_names(page) or []
        shot(page, shots_dir, "J5_fallback_manu_top20")
        ok = (len(checked) == 20)
        R.record("J5", "切到厂商粒度：shown与厂商池交集为空，自动回落到厂商Top20",
                 "PASS" if ok else "FAIL",
                 expected="已勾选数量==20（厂商粒度Top20自动回填）",
                 actual=f"已勾选数量={len(checked)}，名单前5={checked[:5]}")
    safe_run("J5", "非破坏性勾选：J5切到厂商粒度自动回落", j5)

    def j6():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        page.wait_for_timeout(200)
        series = _series_real(get_echarts_series(page))
        names = set(s.get("name") for s in series) if series is not None else set()
        shot(page, shots_dir, "J6_back_to_model")
        ok = (MODEL_A in names) and (MODEL_B in names)
        R.record("J6", f"切回车型粒度：{MODEL_A}、{MODEL_B}两条线仍在", "PASS" if ok else "FAIL",
                 expected=f"折线名字包含 {MODEL_A} 和 {MODEL_B}",
                 actual=f"折线名字={sorted(names)}")
    safe_run("J6", "非破坏性勾选：J6切回车型粒度", j6)

    def j7():
        clear_btn = q(page, "#clearBtn")
        if clear_btn is None:
            R.record("J7", "清除勾选后切换车体类型不应自动填回Top20", "FAIL", detail="找不到 #clearBtn")
            return
        clear_btn.click()
        page.wait_for_timeout(200)
        opts = get_select_options(page, "#bodyTypeSelect") or []
        concrete = next((o for o in opts if o["value"] != "-1"), None)
        if concrete is None:
            R.record("J7", "清除勾选后切换车体类型不应自动填回Top20", "FAIL", detail="下拉框没有具体车体类型")
            return
        set_select(page, "#bodyTypeSelect", concrete["value"])
        series1 = _series_real(get_echarts_series(page))
        set_select(page, "#bodyTypeSelect", "-1")
        series2 = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "J7_cleared_persists")
        ok = (series1 is not None and len(series1) == 0 and series2 is not None and len(series2) == 0)
        R.record("J7", "清除勾选后切换车体类型不应自动填回Top20（userClearedAll 语义要保住）",
                 "PASS" if ok else "FAIL",
                 expected="切到具体车体类型、再切回全部，折线数均应为0",
                 actual=f"切到{concrete['text']}后折线数="
                        f"{len(series1) if series1 is not None else series1}，"
                        f"切回全部后折线数={len(series2) if series2 is not None else series2}")
    safe_run("J7", "非破坏性勾选：J7清除勾选状态不被自动回填打破", j7)

    def j8():
        # 独立重建 J2 的状态，不依赖 J5/J6/J7 留下的中间态。
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_year(page, 2026)
        clear_btn = q(page, "#clearBtn")
        other_toggle = q(page, "#otherToggle")
        if clear_btn is None or other_toggle is None:
            R.record("J8", "「其他」聚合线口径一致性", "FAIL", detail="找不到 #clearBtn 或 #otherToggle")
            return
        clear_btn.click()
        page.wait_for_timeout(150)
        try:
            click_legend_checkbox_by_exact_name(page, MODEL_A)
            click_legend_checkbox_by_exact_name(page, MODEL_B)
        except AssertionError as e:
            R.record("J8", "「其他」聚合线口径一致性", "FAIL", detail=str(e))
            return
        if not other_toggle.is_checked():
            other_toggle.click()
        page.wait_for_timeout(300)
        series = get_echarts_series_full(page)
        shot(page, shots_dir, "J8_other_line")
        if not isinstance(series, list):
            R.record("J8", "「其他」聚合线口径一致性", "FAIL",
                      detail=f"echarts series 读取失败: {series}")
            if other_toggle.is_checked():
                other_toggle.click()
            return
        other = next((s for s in series if s.get("id") == "__other__"), None)
        if other is None:
            R.record("J8", "「其他」聚合线口径一致性", "FAIL",
                      detail=f"没有找到“其他”线。当前series名字={[s.get('name') for s in series]}")
            if other_toggle.is_checked():
                other_toggle.click()
            return
        actual_cum = other.get("data") or []
        # 独立复刻：池子=全部895款车型（车型ytd<=0的那些每月本来就是0，不影响月度合计，
        # 详见 DataOracle.all_models_monthly_totals 的注释），减去已展示的2款。
        total_cum = oracle.all_models_monthly_totals(2026)
        cum_a = oracle.model_monthly_cum(MODEL_A, 2026)
        cum_b = oracle.model_monthly_cum(MODEL_B, 2026)
        if cum_a is None or cum_b is None:
            R.record("J8", "「其他」聚合线口径一致性", "FAIL",
                      detail=f"在RAW里找不到 {MODEL_A} 或 {MODEL_B}")
            if other_toggle.is_checked():
                other_toggle.click()
            return
        expect_cum = [t - a - b for t, a, b in zip(total_cum, cum_a, cum_b)]
        cmp_len = min(len(expect_cum), len(actual_cum))
        mismatches = []
        for idx in range(cmp_len):
            av = actual_cum[idx]
            ev = expect_cum[idx]
            if av is None:
                mismatches.append((idx + 1, av, round(ev)))
                continue
            if abs(float(av) - ev) > 1.0:  # 整数销量，容忍四舍五入误差
                mismatches.append((idx + 1, av, round(ev)))
        ok = (cmp_len > 0) and (not mismatches)
        R.record("J8", "「其他」聚合线 = (池子全部对象合计 − 已展示2款合计)，逐月核对",
                 "PASS" if ok else "FAIL",
                 expected=f"逐月累计值={[round(v) for v in expect_cum]}",
                 actual=f"其他线series.data={actual_cum}，"
                        f"不匹配的(月份,实际,期望)={mismatches[:6]}")
        if other_toggle.is_checked():
            other_toggle.click()
        page.wait_for_timeout(150)
    safe_run("J8", "非破坏性勾选：J8「其他」聚合线口径一致性", j8)


# ============================================================
# K 组 · CSV 结构（新）
# ============================================================

def run_group_K(page, shots_dir, oracle):
    def _download_csv(page):
        btn = q(page, "#tableToggleBtn")
        dl_btn = q(page, "#downloadCsvBtn")
        if btn is None or dl_btn is None:
            return None, None, "找不到 #tableToggleBtn 或 #downloadCsvBtn"
        btn_text = ""
        try:
            btn_text = btn.inner_text()
        except Exception:
            pass
        is_table = ("图表视图" in btn_text)  # 按钮文字是"切换为图表视图"时说明当前已在表格视图
        if not is_table:
            btn.click()
            page.wait_for_timeout(300)
        try:
            with page.expect_download(timeout=5000) as dl_info:
                dl_btn.click()
            download = dl_info.value
            path = download.path()
            text = None
            if path:
                with open(path, "r", encoding="utf-8-sig") as f:
                    text = f.read()
            fname = download.suggested_filename
            return fname, text, None
        except Exception as e:
            return None, None, str(e)
        finally:
            if not is_table:
                try:
                    btn.click()
                    page.wait_for_timeout(150)
                except Exception:
                    pass

    def k1():
        set_gran(page, "manu")
        set_year(page, 2026)
        page.wait_for_timeout(150)
        fname, text, err = _download_csv(page)
        if err or text is None:
            R.record("K1", "CSV第1行必须是表头，不能是#注释行", "FAIL", detail=f"下载/读取失败: {err}")
            return
        lines = text.splitlines()
        first = lines[0] if lines else ""
        ok = bool(lines) and (not first.lstrip().startswith("#")) and ("排名" in first) and ("名称" in first)
        R.record("K1", "CSV第1行必须是表头，不能是#开头的注释行（会破坏Excel筛选/数据透视表）",
                 "PASS" if ok else "FAIL",
                 expected="第1行是含“排名”“名称”的表头，不以 # 开头",
                 actual=f"第1行='{first}'")
    safe_run("K1", "CSV结构：表头必须在第1行", k1)

    def k2():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        el = q(page, "#ownerSelect")
        if el is not None:
            set_select_maybe(page, "#ownerSelect", "manu:一汽红旗")
        set_year(page, 2026)
        page.wait_for_timeout(150)
        fname, text, err = _download_csv(page)
        if err or fname is None:
            R.record("K2", "CSV文件名应含年份/粒度/归属/能源上下文", "FAIL", detail=f"下载失败: {err}")
            set_select_maybe(page, "#ownerSelect", "all")
            return
        ok = (("2026" in fname) and ("车型" in fname) and ("一汽红旗" in fname)
              and (("全部能源" in fname) or ("燃油" in fname) or ("新能源" in fname)))
        R.record("K2", "CSV文件名应含年份/粒度/归属/能源上下文", "PASS" if ok else "FAIL",
                 expected="文件名包含 '2026'、'车型'、'一汽红旗'，以及能源标签之一",
                 actual=f"文件名='{fname}'")
        set_select_maybe(page, "#ownerSelect", "all")
    safe_run("K2", "CSV结构：文件名上下文", k2)



# ============================================================
# L 组 · 年份/能源切换的勾选保持（新，修正5的回归重点）
# ============================================================

def run_group_L(page, shots_dir, oracle):
    MODEL_A = "红旗H5"    # 轿车，2025/2026 两年都有销量
    MODEL_B = "红旗HS5"   # SUV，2025/2026 两年都有销量
    MODEL_C = "零跑A10"   # 2026 年有销量，2025 全年零销量（H7 同款哨兵对象）
    EV1 = "Model Y"       # 纯电，燃油口径下 YTD=0
    EV2 = "海豚"          # 纯电，燃油口径下 YTD=0

    def _series_real(series):
        if not isinstance(series, list):
            return None
        return [s for s in series if s.get("id") != "__other__"]

    def _reset_clean(year=2026, energy="all"):
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        if energy != "all":
            set_energy(page, "all")
        set_year(page, year)
        set_energy(page, energy)
        clear_btn = q(page, "#clearBtn")
        if clear_btn is not None:
            clear_btn.click()
            page.wait_for_timeout(150)
        return clear_btn is not None

    def l1():
        if not _reset_clean(year=2026):
            R.record("L1", f"勾选{MODEL_A}/{MODEL_B}后年份切到2025：图上仍恰好这2条", "FAIL",
                      detail="找不到 #clearBtn，无法建立干净的勾选基线")
            return
        try:
            click_legend_checkbox_by_exact_name(page, MODEL_A)
            click_legend_checkbox_by_exact_name(page, MODEL_B)
        except AssertionError as e:
            R.record("L1", f"勾选{MODEL_A}/{MODEL_B}后年份切到2025：图上仍恰好这2条", "FAIL", detail=str(e))
            return
        page.wait_for_timeout(150)
        series0 = _series_real(get_echarts_series(page))
        names0 = sorted(s.get("name") for s in series0) if series0 is not None else None
        set_year(page, 2025)
        series = _series_real(get_echarts_series(page))
        names = sorted(s.get("name") for s in series) if series is not None else None
        shot(page, shots_dir, "L1_year_2025_kept")
        expect = sorted([MODEL_A, MODEL_B])
        ok = (names0 == expect) and (series is not None and len(series) == 2 and names == expect)
        R.record("L1", f"勾选{MODEL_A}/{MODEL_B}后年份切到2025：图上仍恰好这2条（不是Top20）",
                 "PASS" if ok else "FAIL",
                 expected=f"2026年勾选后={expect}；切到2025年后折线数=2，名字={expect}",
                 actual=f"2026年勾选后折线名字={names0}；切到2025年后折线数="
                        f"{len(series) if series is not None else series}，名字={names}")
    safe_run("L1", "年份切换勾选保持：L1切到2025年", l1)

    def l2():
        set_year(page, 2026)
        series = _series_real(get_echarts_series(page))
        names = sorted(s.get("name") for s in series) if series is not None else None
        shot(page, shots_dir, "L2_year_back_2026")
        expect = sorted([MODEL_A, MODEL_B])
        ok = (series is not None and len(series) == 2 and names == expect)
        R.record("L2", "年份切回2026：仍是恰好这2条", "PASS" if ok else "FAIL",
                 expected=f"折线数=2，名字={expect}",
                 actual=f"折线数={len(series) if series is not None else series}，名字={names}")
    safe_run("L2", "年份切换勾选保持：L2切回2026年", l2)

    def l3():
        if not _reset_clean(year=2026):
            R.record("L3", f"“{MODEL_C}”不在池子(2025零销量)≠从shown删除，切回2026要能回来", "FAIL",
                      detail="找不到 #clearBtn，无法建立干净的勾选基线")
            return
        try:
            click_legend_checkbox_by_exact_name(page, MODEL_C)
            click_legend_checkbox_by_exact_name(page, MODEL_A)
        except AssertionError as e:
            R.record("L3", f"“{MODEL_C}”不在池子(2025零销量)≠从shown删除，切回2026要能回来", "FAIL",
                      detail=str(e))
            return
        page.wait_for_timeout(150)
        series0 = _series_real(get_echarts_series(page))
        names0 = sorted(s.get("name") for s in series0) if series0 is not None else None
        set_year(page, 2025)
        series1 = _series_real(get_echarts_series(page))
        names1 = sorted(s.get("name") for s in series1) if series1 is not None else None
        set_year(page, 2026)
        series2 = _series_real(get_echarts_series(page))
        names2 = sorted(s.get("name") for s in series2) if series2 is not None else None
        shot(page, shots_dir, "L3_zero_year_roundtrip")
        expect0 = sorted([MODEL_A, MODEL_C])
        expect1 = [MODEL_A]  # 零跑A10 2025年零销量，应从图上消失，但只剩1条不该触发Top20回落
        expect2 = sorted([MODEL_A, MODEL_C])
        ok = (names0 == expect0 and names1 == expect1 and names2 == expect2)
        R.record("L3", f"“{MODEL_C}”(2025零销量)切到2025应消失但不删除，切回2026要回来",
                 "PASS" if ok else "FAIL",
                 expected=f"2026勾选后={expect0}；2025年={expect1}（{MODEL_C}消失，{MODEL_A}仍在）；"
                          f"切回2026年={expect2}（{MODEL_C}必须回来）",
                 actual=f"2026勾选后={names0}；2025年={names1}；切回2026年={names2}")
    safe_run("L3", "年份切换勾选保持：L3零销量年份的shown持久性", l3)

    def l4():
        if not _reset_clean(year=2026, energy="all"):
            R.record("L4", "能源切换的勾选保持（新能源↔燃油↔全部）", "FAIL",
                      detail="找不到 #clearBtn，无法建立干净的勾选基线")
            return
        try:
            click_legend_checkbox_by_exact_name(page, EV1)
            click_legend_checkbox_by_exact_name(page, EV2)
        except AssertionError as e:
            R.record("L4", "能源切换的勾选保持（新能源↔燃油↔全部）", "FAIL", detail=str(e))
            return
        page.wait_for_timeout(150)
        expect_ev = sorted([EV1, EV2])

        set_energy(page, "ev")
        series_ev = _series_real(get_echarts_series(page))
        names_ev = sorted(s.get("name") for s in series_ev) if series_ev is not None else None

        set_energy(page, "fuel")
        page.wait_for_timeout(150)
        series_fuel = _series_real(get_echarts_series(page))
        names_fuel = set(s.get("name") for s in series_fuel) if series_fuel is not None else set()
        checked_fuel = legend_checked_names(page) or []

        set_energy(page, "all")
        series_back = _series_real(get_echarts_series(page))
        names_back = sorted(s.get("name") for s in series_back) if series_back is not None else None
        shot(page, shots_dir, "L4_energy_roundtrip")

        ok_ev = (series_ev is not None and len(series_ev) == 2 and names_ev == expect_ev)
        ok_fuel = (EV1 not in names_fuel and EV2 not in names_fuel and len(checked_fuel) == 20)
        ok_back = (series_back is not None and len(series_back) == 2 and names_back == expect_ev)
        ok = ok_ev and ok_fuel and ok_back
        R.record("L4", f"能源切换的勾选保持：新能源保留2条→燃油口径下2款YTD=0应消失并回落Top20→切回全部要回来",
                 "PASS" if ok else "FAIL",
                 expected=f"新能源下折线={expect_ev}；燃油口径下{EV1}/{EV2}都不应出现，且已选数==20（回落Top20）；"
                          f"切回全部后折线={expect_ev}",
                 actual=f"新能源下折线={names_ev}；燃油口径下折线名字集合={names_fuel}，已选数={len(checked_fuel)}；"
                        f"切回全部后折线={names_back}")
    safe_run("L4", "能源切换勾选保持：L4新能源/燃油/全部往返", l4)

    def l5():
        set_year(page, 2026)
        clear_btn = q(page, "#clearBtn")
        if clear_btn is None:
            R.record("L5", "清除勾选后切换年份不应自动填回Top20", "FAIL", detail="找不到 #clearBtn")
            return
        clear_btn.click()
        page.wait_for_timeout(150)
        set_year(page, 2025)
        series1 = _series_real(get_echarts_series(page))
        set_year(page, 2026)
        series2 = _series_real(get_echarts_series(page))
        shot(page, shots_dir, "L5_cleared_persists_year")
        ok = (series1 is not None and len(series1) == 0 and series2 is not None and len(series2) == 0)
        R.record("L5", "清除勾选后切换年份不应自动填回Top20（userClearedAll 语义在年份路径上也要保住）",
                 "PASS" if ok else "FAIL",
                 expected="切到2025年、再切回2026年，折线数均应为0",
                 actual=f"切到2025年后折线数={len(series1) if series1 is not None else series1}，"
                        f"切回2026年后折线数={len(series2) if series2 is not None else series2}")
    safe_run("L5", "年份切换勾选保持：L5清除勾选不被自动回填打破", l5)

    def l6():
        set_gran(page, "model")
        set_select_maybe(page, "#bodyTypeSelect", "-1")
        set_select_maybe(page, "#ownerSelect", "all")
        set_energy(page, "all")
        set_year(page, 2026)
        page.wait_for_timeout(150)
        txt_2026 = inner_text_or_none(page, "#legendCount") or ""
        set_year(page, 2025)
        page.wait_for_timeout(150)
        txt_2025 = inner_text_or_none(page, "#legendCount") or ""
        set_year(page, 2026)
        shot(page, shots_dir, "L6_legendcount_wording")
        ok = ("2026" in txt_2026) and ("2025" in txt_2025) and ("2026" not in txt_2025)
        R.record("L6", "年份切换后 legendCount 措辞应跟着更新为新的当前年份，不残留旧年份",
                 "PASS" if ok else "FAIL",
                 expected="2026年视图下文本含'2026'；切到2025年后文本含'2025'且不再含'2026'",
                 actual=f"2026年文本='{txt_2026}'；2025年文本='{txt_2025}'")
    safe_run("L6", "年份切换勾选保持：L6计数措辞跟随年份更新", l6)

    def l7():
        if not _reset_clean(year=2025):
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL",
                      detail="找不到 #clearBtn，无法建立干净的勾选基线")
            return
        other_toggle = q(page, "#otherToggle")
        if other_toggle is None:
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL",
                      detail="找不到 #otherToggle")
            return
        try:
            click_legend_checkbox_by_exact_name(page, MODEL_A)
            click_legend_checkbox_by_exact_name(page, MODEL_B)
        except AssertionError as e:
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL", detail=str(e))
            return
        if not other_toggle.is_checked():
            other_toggle.click()
        page.wait_for_timeout(300)
        series = get_echarts_series_full(page)
        shot(page, shots_dir, "L7_other_line_2025")
        if not isinstance(series, list):
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL",
                      detail=f"echarts series 读取失败: {series}")
            if other_toggle.is_checked():
                other_toggle.click()
            set_year(page, 2026)
            return
        other = next((s for s in series if s.get("id") == "__other__"), None)
        if other is None:
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL",
                      detail=f"没有找到“其他”线。当前series名字={[s.get('name') for s in series]}")
            if other_toggle.is_checked():
                other_toggle.click()
            set_year(page, 2026)
            return
        actual_cum = other.get("data") or []
        total_cum = oracle.all_models_monthly_totals(2025)
        cum_a = oracle.model_monthly_cum(MODEL_A, 2025)
        cum_b = oracle.model_monthly_cum(MODEL_B, 2025)
        if cum_a is None or cum_b is None:
            R.record("L7", "切到2025年后「其他」聚合线口径仍自洽（逐月核对）", "FAIL",
                      detail=f"在RAW里找不到 {MODEL_A} 或 {MODEL_B}")
            if other_toggle.is_checked():
                other_toggle.click()
            set_year(page, 2026)
            return
        expect_cum = [t - a - b for t, a, b in zip(total_cum, cum_a, cum_b)]
        cmp_len = min(len(expect_cum), len(actual_cum))
        mismatches = []
        for idx in range(cmp_len):
            av = actual_cum[idx]
            ev = expect_cum[idx]
            if av is None:
                mismatches.append((idx + 1, av, round(ev)))
                continue
            if abs(float(av) - ev) > 1.0:
                mismatches.append((idx + 1, av, round(ev)))
        ok = (cmp_len > 0) and (not mismatches)
        R.record("L7", "切到2025年后「其他」聚合线 = (2025池子全部对象合计 − 已展示2款合计)，逐月核对",
                 "PASS" if ok else "FAIL",
                 expected=f"逐月累计值={[round(v) for v in expect_cum]}",
                 actual=f"其他线series.data={actual_cum}，不匹配的(月份,实际,期望)={mismatches[:6]}")
        if other_toggle.is_checked():
            other_toggle.click()
        set_year(page, 2026)
        page.wait_for_timeout(150)
    safe_run("L7", "年份切换勾选保持：L7其他聚合线口径(2025年)", l7)


# ============================================================
# 主流程
# ============================================================

def main():
    # 默认路径必须相对于本脚本所在的仓库推导，不能写死绝对路径。
    # 写死绝对路径的后果不是"报错找不到文件"（那还算好的），而是更危险的假绿灯：
    # 如果那个绝对路径下恰好存在一份别的、过期的产物，测试会对着错误的文件跑出
    # 一个漂亮的全绿结果，而仓库里真正改动的 docs/index.html 根本没被测到。
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default=os.path.join(repo_root, "docs", "index.html"))
    ap.add_argument("--shots", default=os.path.join(repo_root, "tests", "shots_scope"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    html_path = os.path.abspath(args.html)
    shots_dir = os.path.abspath(args.shots)
    os.makedirs(shots_dir, exist_ok=True)

    if not os.path.isfile(html_path):
        print(f"找不到 HTML 文件: {html_path}", file=sys.stderr)
        sys.exit(2)

    with open(html_path, "r", encoding="utf-8") as f:
        html_text = f.read()

    raw, meta, extract_errs = extract_raw_meta(html_text)
    if raw is None or meta is None:
        R.record("SETUP", "从 HTML 提取 RAW/META 数据", "FAIL",
                 detail="; ".join(extract_errs))
        print("警告: 无法从 HTML 提取 RAW/META，所有依赖 Python 侧口径复刻的用例将标记为 FAIL 并继续。",
              file=sys.stderr)
        oracle = None
    else:
        oracle = DataOracle(raw, meta)

    console_errors = []
    page_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        def on_console(msg):
            if msg.type == "error":
                console_errors.append(f"[console.error] {msg.text}")

        def on_pageerror(exc):
            page_errors.append(f"[pageerror] {exc}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

        uri = "file://" + html_path
        try:
            page.goto(uri, timeout=30000)
            page.wait_for_selector("#chart", timeout=15000)
            page.wait_for_timeout(600)
        except Exception as e:
            R.record("SETUP", "打开页面", "FAIL", detail=f"page.goto/等待 #chart 失败: {e}")
            R.print_table()
            if args.json:
                with open(args.json, "w", encoding="utf-8") as jf:
                    jf.write(R.to_json())
            browser.close()
            sys.exit(1)

        # ---- A 组：基础健康（在其余交互开始前先记录，避免后续操作产生的 console 消息误伤 A1）----
        run_group_A(page, shots_dir, console_errors, page_errors)

        if oracle is not None:
            run_group_B(page, shots_dir, oracle)
            run_group_C(page, shots_dir, oracle)
            run_group_D(page, shots_dir, oracle)
            run_group_E(page, shots_dir, oracle)
        else:
            for gid, name in [("B1", "bodyTypeSelect 首项"), ("B2", "全部车体类型默认选中"),
                               ("B3", "全部车体类型池子大小"),
                               ("C1", "ownerGroup可见性"), ("C2", "ownerSelect结构"),
                               ("C3", "归属筛选核心"), ("C4", "折线数一致性"),
                               ("C5", "品牌口径"), ("C6", "归属+能源交集"), ("C7", "reset/clear"),
                               ("D1", "标题含归属"), ("D2", "标题含全部车体类型"),
                               ("D3", "排名标签含比较池"), ("D4", "排名标签简洁"),
                               ("D5", "其他线名含范围"),
                               ("E1", "厂商下钻按钮"), ("E2", "车型无下钻按钮"),
                               ("E3", "下钻后状态"), ("E4", "品牌下钻")]:
                R.record(gid, name, "FAIL", detail="RAW/META 提取失败，无法建立 Python 侧口径参照")

        run_group_F(page, shots_dir)
        run_group_G(page, shots_dir, oracle if oracle is not None else DummyOracle())

        if oracle is not None:
            run_group_H(page, shots_dir, oracle)
            run_group_I(page, shots_dir, oracle)
            run_group_J(page, shots_dir, oracle)
            run_group_K(page, shots_dir, oracle)
        else:
            for gid, name in [("H1", "厂商粒度池子大小"), ("H2", "品牌粒度池子大小"),
                               ("H3", "车型粒度池子大小"), ("H4", "逐项零销量校验"),
                               ("H5", "跨年份池子校验"), ("H6", "排名连续性"),
                               ("H7", "新增对象同比哨兵"),
                               ("I1", "header措辞"), ("I2", "图例计数措辞"),
                               ("J1", "清除勾选基线"), ("J2", "手动勾选2个"),
                               ("J3", "切到SUV"), ("J4", "切回全部车体类型(核心)"),
                               ("J5", "切到厂商粒度回落"), ("J6", "切回车型粒度"),
                               ("J7", "清除勾选后不自动回填"), ("J8", "其他聚合线口径一致性"),
                               ("K1", "CSV表头在第1行"), ("K2", "CSV文件名上下文")]:
                R.record(gid, name, "FAIL", detail="RAW/META 提取失败，无法建立 Python 侧口径参照")

        if oracle is not None:
            run_group_L(page, shots_dir, oracle)
        else:
            for gid, name in [("L1", "年份切换保留勾选"), ("L2", "年份切回2026"),
                               ("L3", "零销量年份shown持久性"), ("L4", "能源切换保留勾选"),
                               ("L5", "清除勾选年份路径不回填"), ("L6", "计数措辞随年份更新"),
                               ("L7", "其他聚合线口径(2025年)")]:
                R.record(gid, name, "FAIL", detail="RAW/META 提取失败，无法建立 Python 侧口径参照")

        browser.close()

    R.print_table()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as jf:
            jf.write(R.to_json())
        print(f"\n结构化结果已写入: {args.json}")

    print(f"\n截图目录: {shots_dir}")

    sys.exit(1 if R.has_fail() else 0)


class DummyOracle:
    """RAW/META 提取失败时的占位对象，避免 G1 等依赖 oracle 的用例直接崩掉整个脚本。"""
    def yoy_pct(self, *a, **kw):
        raise AssertionError("RAW/META 提取失败，无法计算参照值")


if __name__ == "__main__":
    main()
