# src/ —— 本项目的真源码

这个目录下的文件就是实际跑起来的代码，**改这里就是改行为**。workflow 直接用
`python3 src/xxx.py` 调用它们：

| 文件 | 跑它的 workflow | 干什么 |
|---|---|---|
| `sync_script.py` | `.github/workflows/probe.yml`（name: `Sync Car Sales Data (16888)`） | 抓车主之家月度销量，写 `data/sales.csv`、`data/manufacturers.txt`、`data/prices.json`、`data/sync_report.md` |
| `build.py` | `.github/workflows/build.yml` | 把 `data/` 下的数据构建成单文件看板 `docs/index.html` |
| `notify_feishu.py` | `.github/workflows/probe.yml`（同步跑完后） | 把同步结果推到飞书机器人 |
| `probe_news.py` | `.github/workflows/probe_news.yml` | 新闻源探针 |

改动流程、本地重建和回归测试怎么跑，见**仓库根 README 第七节「修改代码的正确姿势」**。

---

## 历史：这份说明以前是错的

这个文件之前写的是「src/ 是运行时自动生成的源码归档，只供阅读和 diff，改这里不会影响
任何行为，要改请去改 workflow YAML 里的内嵌脚本」。

那段话描述的是项目早期的做法：脚本以 heredoc 形式内嵌在 workflow YAML 里，`src/` 只是
每次运行时导出的一份快照。**这套流程早就取消了**（原因见根 README 第七节末尾：内嵌
heredoc 当初是为了让非技术用户只粘贴一个文件就能完成首次部署，代价是之后每次更新都要
粘贴十几万字的 YAML、缩进极易出错、diff 完全不可读；push 自动化之后这个取舍不再成立）。
取消时漏改了这个文件，于是它在仓库里继续误导了一段时间。

`tools/` 下的 `build_workflow_yml.py` / `build_yml.py` 是那个时代的生成器，**已经不是
必经步骤**，并且里面还写着 `/tmp/p2-chart/` 这类早期的本地路径，不要照着跑。

留着这段记录，是因为"文档说改 A，实际要改 B"这种错误会让人改错文件而且很难自查 ——
2026-10 修能源标注那次就差点栽在这上面。以后若再调整脚本的组织方式，记得回头改这里。
