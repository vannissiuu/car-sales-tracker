import re

with open('/tmp/p1-sync/sync_script.py', 'r', encoding='utf-8') as f:
    script = f.read()

with open('/tmp/p4-feishu/notify_feishu.py', 'r', encoding='utf-8') as f:
    notify_script = f.read()

indent = ' ' * 10


def indent_block(text):
    return '\n'.join((indent + line if line.strip() else '') for line in text.split('\n'))


indented_script = indent_block(script)
indented_notify_script = indent_block(notify_script)

header = '''name: Sync Car Sales Data (16888)

# 三种触发方式：
#   1. workflow_dispatch —— 手动点击触发，可以自定义 max_months / force_refresh
#   2. push（限 .github/workflows/** 路径）—— 工作流文件本身改动时自动跑一次，方便验证改动没有写挂
#   3. schedule —— 每月自动抓最新一期销量数据，不需要人工操作
#
# 国内月度销量数据通常在次月 8-16 日陆续发布，具体哪天不固定，所以 cron 定了 5 个候选日期
# （8/10/12/14/16 日）都跑一次；脚本本身是幂等的（已经抓过的月份直接跳过退出），
# 数据没发布的那几次运行只是"确认了一下还没有新数据"，不会重复抓取或产生重复数据，
# 只会在数据真正发布的那天成功抓到一次。
on:
  workflow_dispatch:
    inputs:
      max_months:
        description: '本次最多处理几个月（0 = 不限制，跑完全部）'
        required: false
        default: '1'
      force_refresh:
        description: '重抓已存在的月份（true = 忽略已有数据全部重抓）'
        required: false
        default: 'false'
  push:
    branches:
      - main
      - master
    paths:
      - '.github/workflows/**'
  schedule:
    - cron: '0 4 8,10,12,14,16 * *'   # 每月 8/10/12/14/16 日 04:00 UTC = 北京时间 12:00

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    permissions:
      contents: write
    # 就算脚本内部出了没兜住的异常，也不要让整个 job 显示失败——
    # 抓取失败/被拦截本身就是我们要收集的结论，不是流程错误
    continue-on-error: true
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Python dependencies
        run: pip install requests beautifulsoup4 lxml pandas

      - name: Write sync script
        run: |
          mkdir -p src
          cat > src/README.md << 'SRC_README_EOF'
          # src/ —— 运行时自动生成的源码归档

          本目录下的文件由 GitHub Actions 工作流每次运行时，从 workflow YAML 里内嵌的脚本
          自动重新写出，目的是让源码在仓库里有独立、可读、可 diff 的历史，不必打开 YAML
          文件翻找 heredoc 才能看代码。

          - 本目录**只供阅读和 diff**，直接修改这里的文件不会影响任何工作流的实际行为——
            下一次运行时会被内嵌在 workflow 里的版本原样重写覆盖。
          - 想要真正修改脚本行为，请改动 workflow YAML 文件里对应的内嵌脚本内容，
            改动流程见仓库根 README 第七节。
          SRC_README_EOF
          cat > src/sync_script.py << 'SYNC_SCRIPT_EOF'
'''

middle_after_sync_script = '''          SYNC_SCRIPT_EOF

      - name: Run sync script
        # 脚本内部已经用 try/except 兜住了几乎所有异常，
        # 这里再加一层 continue-on-error 保险，确保后面的 commit/upload 步骤一定能跑。
        # 给这一步起个 id，是因为 sync.py 运行结束时会把"本次是否发生了值得关注的事情"
        # (noteworthy: 新抓到数据 / 有失败放弃 / 被拦截 三者任一为真) 写进
        # $GITHUB_OUTPUT，后面的"发送飞书通知"步骤要读这个值来判断该不该发通知——
        # 纯粹的幂等空跑（比如月初 8 号数据还没发布）不应该产生通知噪音。
        id: run_sync
        continue-on-error: true
        env:
          # push/schedule 触发时没有 inputs.max_months/force_refresh，两个都会是空字符串——
          # sync.py 里 parse_max_months()/parse_force_refresh() 把空字符串分别按
          # "只跑1个月"/"不重抓" 处理（保守优先），不会因为一次 push 或定时触发就误跑全量、
          # 误重抓。定时任务本来就只需要抓"当前缺失的最新月份"，MAX_MONTHS=1（留空时的默认值）
          # 正合适，不用特意在这里为 schedule 分支设置成别的值。
          MAX_MONTHS: ${{ inputs.max_months }}
          FORCE_REFRESH: ${{ inputs.force_refresh }}
          # 报告里记一笔这次是怎么触发的（schedule / workflow_dispatch / push），方便事后追查。
          TRIGGER_EVENT: ${{ github.event_name }}
        run: python3 src/sync_script.py

      - name: Drop oversized files before commit
        if: always()
        continue-on-error: true
        run: |
          find data/ -type f -size +50M -print -exec sh -c 'echo "文件过大已删除: $(basename "$1")" > "$1.toolarge.txt"; rm -f "$1"' _ {} \; || true

      - name: Commit sync results back to repo
        # 定时任务开始跑之后，sync 和其它手动触发/别的工作流撞车（同时往仓库 push）的概率
        # 变高了——直接 git push 一旦撞上 non-fast-forward 会被拒绝，之前 continue-on-error
        # 会把这类失败整个吞掉，日志上完全看不出发生过什么。现在改成显式重试：push 失败就
        # git pull --rebase --autostash 再试，最多 3 次，间隔递增；三次都失败时打印醒目的
        # 失败信息（不再静默），但步骤本身仍然 continue-on-error —— 数据已经在 artifact 里，
        # 不该因为一次推送竞争让整个 job 显示失败。
        if: always()
        continue-on-error: true
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A data/ src/ || true
          if git diff --staged --quiet; then
            echo "没有变化，跳过提交"
          else
            git commit -m "chore: sync car sales data (16888) [skip ci]"
            for i in 1 2 3; do
              if git push; then
                echo "push 成功"
                break
              fi
              echo "push 失败（第 $i 次），拉取远端后重试 ..."
              git pull --rebase --autostash || true
              sleep $((i * 5))
              if [ "$i" = "3" ]; then
                echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
                echo "!! git push 连续 3 次失败，data/ 的更新没有推送成功，仓库里看到的"
                echo "!! 还是旧数据。本次抓到的数据仍然在 artifact 里，可以手动下载，"
                echo "!! 但需要人工介入排查为什么远端一直推不上去。"
                echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
              fi
            done
          fi

      - name: Upload sync output
        # 关键：即使上面的步骤失败，也要执行这一步，
        # 否则运行失败时我们什么产出都拿不到
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sales-data
          path: data/
          if-no-files-found: warn
          retention-days: 30

      - name: Write Feishu notify script
        if: always()
        run: |
          mkdir -p src
          cat > src/notify_feishu.py << 'NOTIFY_SCRIPT_EOF'
'''

footer_after_notify_script = '''          NOTIFY_SCRIPT_EOF

      - name: Send Feishu notification
        # 条件解释：
        #   - always() 保证就算前面的步骤失败/被跳过，这一步依然有机会跑（通知本身也包括
        #     "失败了要不要告警"这一类场景，不能因为前面失败就不发通知）。
        #   - steps.run_sync.outputs.noteworthy != 'false'：只有在明确知道"这次纯粹是
        #     幂等空跑、什么都没发生"（noteworthy 显式写成了 'false'）时才跳过。这个值
        #     没写出来（比如 sync.py 在写出这个值之前就整个崩溃了）时按"!= 'false'"算，
        #     即默认发送——宁可多发一条，也不要把一次真正的崩溃悄悄吞掉不通知。
        #   - steps.run_sync.outcome == 'failure'：即使上面判断不出 noteworthy（罕见的
        #     环境级失败，比如 python3 都跑不起来），只要这一步本身标记为失败，也强制发送。
        # notify_feishu.py 内部自己会兜住"FEISHU_WEBHOOK 没配置"的情况，直接打印说明后
        # exit(0)，不会因为用户还没配置 Secret 就让这一步失败。
        if: |
          always() &&
          (steps.run_sync.outputs.noteworthy != 'false' || steps.run_sync.outcome == 'failure')
        continue-on-error: true
        env:
          FEISHU_WEBHOOK: ${{ secrets.FEISHU_WEBHOOK }}
          FEISHU_SECRET: ${{ secrets.FEISHU_SECRET }}
          DASHBOARD_URL: https://vannissiuu.github.io/car-sales-tracker/
          # 把同步步骤的真实结果原样传给通知脚本，脚本自己会先判"这次同步跑没跑成"
          # 再决定要不要信任 sync_report.md 的内容——绝不能只靠报告文件本身的内容
          # 判断本次状态，它随时可能是上一次成功运行遗留下来的旧文件。
          SYNC_OUTCOME: ${{ steps.run_sync.outcome }}
          SYNC_NOTEWORTHY: ${{ steps.run_sync.outputs.noteworthy }}
          GITHUB_RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: python3 src/notify_feishu.py
'''

full = (
    header
    + indented_script
    + '\n'
    + middle_after_sync_script
    + indented_notify_script
    + '\n'
    + footer_after_notify_script
)

with open('/tmp/p1-sync/.github/workflows/sync.yml', 'w', encoding='utf-8') as f:
    f.write(full)

print("written", len(full), "chars")
