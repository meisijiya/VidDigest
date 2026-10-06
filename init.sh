#!/bin/bash
# VidDigest 验证门禁。退出码 0 之前，任何工单都不得标记为完成。
#
# 跑法（Windows + Git Bash）：
#   cd 'D:\26code\简历项目\universal-video-downloader'
#   & 'C:\Program Files\Git\bin\bash.exe' ./init.sh
#
# 这里的解释器路径是硬约束，不是风格问题：系统 Python 版本不对，
# 必须用 backend 自己的 venv。AGENTS.md「环境事实」一节记的是同一条。

set -e

echo "=== VidDigest Verification ==="

explain_failure() {
  echo ""
  echo "=== Verification FAILED ==="
  echo "要么基线已经坏了，要么上面的检查真的失败了。"
  echo "先修基线，再重跑 ./init.sh。"
  echo "在 ./init.sh 退出 0 之前，不要把任何工单标记为完成。"
}
trap explain_failure ERR

# 真正跑起来的检查数。计数为 0 时门禁必须失败：
# 一个跑不了任何检查却打印 "Verification Complete" 并退出 0 的门禁不是门禁。
RAN=0

# ── 后端：测试 ──────────────────────────────────────────────
PY="backend/venv/Scripts/python.exe"

if [ -x "$PY" ]; then
  echo ""
  echo "=== backend: pytest ==="
  ( cd backend && ./venv/Scripts/python.exe -m pytest tests -q )
  RAN=$((RAN + 1))

  # 静态检查：编译全部后端源码，跳过 venv 与缓存目录。
  # 用的是同一个 venv 解释器，因此不依赖系统 PATH 上的 python 版本。
  echo ""
  echo "=== backend: compileall ==="
  ( cd backend && ./venv/Scripts/python.exe -m compileall -q \
      -x '(^|[\\/])(\.?venv|env|node_modules|build|dist|__pycache__)([\\/]|$)' \
      -x 'venv[\\/]' . )
  RAN=$((RAN + 1))

  # 敏感内容扫描（第 4 道关卡）。
  #
  # 为什么它必须在门禁里，而不是只写成 .gitignore 里的一条注释：
  # .gitignore 是**文件名级**的。它挡得住 .env，挡不住「把 .env 里那条口令
  # 抄进 docs/OPERATIONS.md」。本仓库真的发生过一次——TEST_ACCOUNTS.md 按
  # 规矩移出了版本库，可同一批明文口令与本机用户名留在了运维手册和一个
  # agent 日志目录里，直到推上 PUBLIC 远端。护栏必须在**内容**上。
  #
  # 这条检查自身有变异测试兜着：每种要抓的形态都故意放回去确认它转红，
  # 否则「跑完是绿的」证明不了关卡有效。见 scripts/mutation_secrets_gate.py。
  #
  # 用 $PY（仓库根相对）而不是 ./venv/...：上面两关跑在 ( cd backend && … )
  # 子 shell 里，cwd 改动不外泄，所以这一关仍在仓库根 —— 写 ./venv/Scripts
  # 会得到 "No such file or directory"，退出码 127。检查脚本自己按 __file__
  # 推算仓库根，与 cwd 无关。
  echo ""
  echo "=== secrets: content-level scan of tracked files ==="
  "$PY" scripts/check_secrets.py
  RAN=$((RAN + 1))
else
  echo "ERROR: 找不到 $PY —— 后端虚拟环境没装好，门禁拒绝通过。"
  echo "在 backend/ 下重建 venv 后重跑。"
  exit 1
fi

# ── 前端：测试 ──────────────────────────────────────────────
if [ -f frontend/package.json ]; then
  echo ""
  echo "=== frontend: npm test (静态 / 契约断言) ==="
  ( cd frontend && npm test )
  RAN=$((RAN + 1))

  # 挂载层（工单 #18）。**单独一关**，不是上面那条的一部分：
  # 两套 runner 守的是两类判据，混在一处就看不出是哪一类红了——
  # 而「绿色的那一层」是哪一层，正是工单 #18 要回答的问题。
  #
  # 只收 tests/*.spec.mjs（vitest.config.js 的 include 写死了）。默认
  # include 会顺手吃掉 node:test 的 *.test.mjs，而 node:test 的
  # describe 体抛异常时 vitest 仍退出 0 —— 那会让这一关**恒绿**。
  # 「一条都没跑却报绿」不是可能性，是已实测过的形态。
  echo ""
  echo "=== frontend: npm run test:mount (真挂载) ==="
  ( cd frontend && npm run test:mount )
  RAN=$((RAN + 1))
else
  echo "ERROR: 找不到 frontend/package.json，门禁拒绝通过。"
  exit 1
fi

if [ "$RAN" -eq 0 ]; then
  echo ""
  echo "ERROR: 一条检查都没真跑过。"
  echo "这个门禁不能失败，所以它不算门禁。修好上面的分支再重跑。"
  exit 1
fi

echo ""
echo "=== Verification Complete ($RAN checks) ==="
echo ""
echo "下一步："
echo "1. 读 AGENTS.md —— 启动路径、工作规则、完成定义、状态在哪"
echo "2. gh issue list --state open —— 挑一张阻塞边清楚的工单"
echo "3. 只实现那一张，不要越界"
echo "4. 收尾前重跑本脚本；退出 0 才能在工单里留证据"
echo "5. 状态与阻塞边归 to-tickets，会话交接归 handoff，本文件不记录任何进度状态"
