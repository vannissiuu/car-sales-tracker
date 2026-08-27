# 中国汽车销量看板

<p align="center">
  <a href="https://vannissiuu.github.io/car-sales-tracker/">
    <img src="./assets/readme/hero.svg" width="100%" alt="中国汽车销量看板：按厂商、品牌、车体类型和能源类型探索月度销量趋势">
  </a>
</p>

<p align="center">
  <a href="https://vannissiuu.github.io/car-sales-tracker/">打开在线看板</a>
  ·
  <a href="./data/sales.csv">查看原始数据</a>
  ·
  <a href="./data/sync_report.md">查看同步报告</a>
</p>

把公开的月度车型销量整理成一张可交互、可追溯的趋势看板。你可以在厂商、品牌、车体类型 → 车型之间切换，比较燃油与新能源，并从图表一路核对到纳入统计的具体车型。

## 能做什么

- **多层级趋势**：按厂商、品牌或车型查看年初至今累计销量与月度变化。
- **可控筛选**：切换年份、能源类型、车体类型和图表模式；搜索并手动加入关注对象。
- **读图与核对**：折线图、堆积面积图和表格视图可互换；点击对象查看月度明细、同比与统计范围。
- **主题与导出**：支持浅色 / 深色主题；表格视图可下载当前筛选结果为 CSV。
- **静态部署**：构建产物是一个自包含的 `docs/index.html`，适合直接托管到 GitHub Pages，运行时无需后端。

## 数据快照

| 覆盖范围 | 记录 | 厂商 | 品牌 | 车型 |
| --- | ---: | ---: | ---: | ---: |
| 2024 年 1 月 – 2026 年 7 月 | 18,814 行 | 117 | 121 | 895 |

数据文件：[`data/sales.csv`](./data/sales.csv) · 品牌归并规则：[`data/mapping.json`](./data/mapping.json)

## 数据来源与口径

当前数据由同步工作流从车主之家（16888.com）月度销量排行榜采集，并规范为以下字段：

```text
year, month, manufacturer, brand, model, body_type, energy_type, sales
```

销量口径不是数据源页面的官方声明，而是基于与乘联会（CPCA）零售 / 批发数据的逐月比对得出的高置信度推断：更接近**全国乘用车市场零售**，不应直接与批发、交强险上牌量或中汽协产销口径混比。详细的比对过程、异常和未完成月份见 [`data/sync_report.md`](./data/sync_report.md)。

品牌不是简单复制厂商字段：优先按车型映射，其次按厂商映射，最后才回退到厂商原值；一厂多牌和同品牌多厂商均按车型逐条归并，规则见 [`data/mapping.json`](./data/mapping.json)。

## 工作流

```text
车主之家月榜
    ↓  定时抓取 / 拦截检测 / 完整月份校验
data/sales.csv + data/mapping.json
    ↓  聚合厂商、品牌、车型与能源类型
docs/index.html
    ↓  GitHub Pages
在线交互式看板
```

- [`Sync Car Sales Data (16888)`](./.github/workflows/probe.yml)：每月 8、10、12、14、16 日定时尝试同步，也支持手动运行。
- [`Build Car Sales Dashboard`](./.github/workflows/build.yml)：同步成功后生成看板，避免用残缺数据覆盖线上产物。
- `probe_*` 目录保留来源探测、页面文本、接口字段与截图，便于复核抓取条件和数据结构。

## 本地查看

看板已经是静态文件，直接启动一个本地静态服务器即可：

```bash
git clone https://github.com/vannissiuu/car-sales-tracker.git
cd car-sales-tracker
python3 -m http.server 8000 --directory docs
```

然后打开 <http://127.0.0.1:8000>。

如果只想查看数据或规则，无需运行看板：

- [`data/sales.csv`](./data/sales.csv)：车型级月度销量明细。
- [`data/manufacturers.txt`](./data/manufacturers.txt)：抓取到的厂商清单。
- [`data/mapping.json`](./data/mapping.json)：品牌归并字典与未决说明。
- [`data/sync_report.md`](./data/sync_report.md)：最近一次同步的结果与数据质量记录。

## 目录

| 路径 | 用途 |
| --- | --- |
| [`docs/index.html`](./docs/index.html) | 自包含交互式看板 |
| [`data/`](./data) | 数据、映射和同步报告 |
| [`probe_output/`](./probe_output) | 汽车销量来源探测证据 |
| [`probe_news_output/`](./probe_news_output) | 新闻来源探测结果 |
| [`probe_v3_output/`](./probe_v3_output) | 新版抓取逻辑验证结果 |
| [`.github/workflows/`](./.github/workflows) | 同步与构建工作流 |

## 说明

- 当前数据截至 2026 年 7 月；未完成抓取的月份不会被写入，后续月份也不会被补零。
- `其他` 表示未能从车体类型榜单归类的车型，不等同于“没有车身类型”。
- 这是数据整理与分析工具，不构成购车、投资或市场预测建议。
