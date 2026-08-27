import re

with open('/tmp/p2-chart/build.py', 'r', encoding='utf-8') as f:
    script = f.read()

indent = ' ' * 10
indented_script = '\n'.join(
    (indent + line if line.strip() else '')
    for line in script.split('\n')
)

header = '''name: Build Car Sales Dashboard

# 手动点击触发，或者 sync 工作流跑完之后自动触发（sync 提交数据用 [skip ci]，
# 普通 push 触发会被挡掉，所以这里必须用 workflow_run 监听 sync 工作流本身的完成事件）。
# 注意：workflow_run 的 workflows 列表里必须填 sync 工作流 name 字段的准确值
# （即 /tmp/p1-sync/.github/workflows/sync.yml 第一行 name: 的值），
# 名字对不上这个触发器永远不会触发。
on:
  workflow_dispatch: {}
  workflow_run:
    workflows: ["Sync Car Sales Data (16888)"]
    types: [completed]

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: write
    # 只在手动触发，或者 sync 工作流本次运行成功时才构建——
    # sync 失败/中止时不要拿残缺或过期的 data/sales.csv 去覆盖线上看板。
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}
    # 就算脚本内部出了没兜住的异常，也不要让整个 job 显示失败——
    # 后面的 commit/upload 步骤仍然要有机会跑，尽量拿到部分产出
    continue-on-error: true
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          # workflow_run 触发默认 checkout 的是默认分支最新提交，不一定是触发本次运行的
          # 那个 sync 提交。显式指定 head_sha，确保拿到的是 sync 刚提交完 data/sales.csv
          # 之后的那个 commit；workflow_dispatch 手动触发时 workflow_run 上下文不存在，
          # 回退到 github.ref（手动运行时选择的分支/引用）。
          ref: ${{ github.event.workflow_run.head_sha || github.ref }}

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Write build script
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
          cat > src/build.py << 'BUILD_PY_EOF'
'''

footer_after_script = '''          BUILD_PY_EOF

      - name: Run build script
        # build.py 内部对 ECharts 获取失败等情况已有多级回退（本地缓存 -> npm pack ->
        # CDN 直连 -> 最后回退成 <script src=CDN> 标签），这里再加一层 continue-on-error
        # 保险，确保就算构建整体失败，后面的 commit/upload 步骤也仍有机会执行、暴露问题。
        continue-on-error: true
        run: python3 src/build.py

      - name: Commit dashboard back to repo
        # 已知问题的修复：新闻探针等其它工作流也可能在这段时间往仓库推 docs/ 之外的改动，
        # 或者本工作流的上一次运行还没完全跑完，导致 push 撞上 non-fast-forward 被拒绝。
        # 之前 continue-on-error 会把这类失败直接吞掉、日志上完全看不出发生过什么。
        # 现在改成显式重试：push 失败就 git pull --rebase --autostash 再试，最多 3 次，
        # 间隔递增；三次都失败时打印醒目的失败信息（不再静默），
        # 但步骤本身仍然 continue-on-error —— 构建产物已经进 artifact 了，
        # 不应该因为一次推送竞争让整个 job 显示失败。
        if: always()
        continue-on-error: true
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -A docs/ src/ || true
          if git diff --staged --quiet; then
            echo "没有变化，跳过提交"
          else
            git commit -m "chore: build dashboard [skip ci]"
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
                echo "!! git push 连续 3 次失败，docs/ 的更新没有推送成功，仓库里看到的"
                echo "!! 还是旧看板。构建产物仍然在本次运行的 artifact 里，可以手动下载，"
                echo "!! 但需要人工介入排查为什么远端一直推不上去。"
                echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
              fi
            done
          fi

      - name: Upload build output
        # 关键：即使上面的步骤失败，也要执行这一步，
        # 否则运行失败时我们什么产出都拿不到
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: dashboard-site
          path: docs/
          if-no-files-found: warn
          retention-days: 30
'''

full = header + indented_script + '\n' + footer_after_script

with open('/tmp/p2-build/.github/workflows/build.yml', 'w', encoding='utf-8') as f:
    f.write(full)

print("written", len(full), "chars")
