#!/usr/bin/env bash
# 把门禁需要的环境从零装起来：后端虚拟环境 + 前端依赖。
#
# **为什么要有它**：backend/venv 与 frontend/node_modules 都不在版本库里
# （.gitignore 排除）。没有这个脚本，别人 clone 下来第一件事就是
# 「门禁跑不起来」，只能去问作者。
# **一个只有作者能跑的门禁，等于没有门禁。**
#
# 跑法：bash scripts/bootstrap.sh

set -e

cd "$(dirname "$0")/.."
echo "=== repository root: $(pwd) ==="

# ── 用来建虚拟环境的解释器 ─────────────────────────────────
if command -v python3 >/dev/null 2>&1; then
  BOOT_PY="python3"
elif command -v python >/dev/null 2>&1; then
  BOOT_PY="python"
else
  echo "ERROR: 找不到 python3 / python，装不了后端虚拟环境。" >&2
  exit 1
fi
echo "=== bootstrap python: $($BOOT_PY --version 2>&1) ==="

# ── 后端虚拟环境 ──────────────────────────────────────────
if [ -x "backend/venv/Scripts/python.exe" ] || [ -x "backend/venv/bin/python" ]; then
  echo "=== backend: 虚拟环境已存在，跳过创建 ==="
else
  echo "=== backend: 创建虚拟环境 ==="
  "$BOOT_PY" -m venv backend/venv
fi

if [ -x "backend/venv/Scripts/python.exe" ]; then
  VENV_PY="backend/venv/Scripts/python.exe"
else
  VENV_PY="backend/venv/bin/python"
fi

echo "=== backend: 安装依赖 ==="
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# ── 前端依赖 ──────────────────────────────────────────────
echo "=== frontend: 安装依赖（npm ci 走锁文件）==="
cd frontend
npm ci
