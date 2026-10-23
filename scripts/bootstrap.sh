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
# 解释器必须**真的能跑**，不是「文件在」就算。
# 把 Windows 的 venv 拷到 Linux（或反过来）时，文件在、执行位也在，
# 但跑起来是 Exec format error——拿一个坏解释器往下走，
# 报出来的错会指向 pip 或依赖，而不是「这个 venv 不是这台机器的」。
VENV_PY=""
if [ -x "backend/venv/bin/python" ] && backend/venv/bin/python --version >/dev/null 2>&1; then
  VENV_PY="backend/venv/bin/python"
elif [ -x "backend/venv/Scripts/python.exe" ] && backend/venv/Scripts/python.exe --version >/dev/null 2>&1; then
  VENV_PY="backend/venv/Scripts/python.exe"
else
  if [ -d "backend/venv" ]; then
    echo "=== backend: 现有虚拟环境跑不起来（多半是别的操作系统建的），重建 ==="
    # venv 不在版本库里、可重建，删掉是安全的；删不掉就让用户自己处理。
    rm -rf backend/venv || {
      echo "ERROR: 删不掉 backend/venv，请手动删除后重跑。" >&2
      exit 1
    }
  fi
  echo "=== backend: 创建虚拟环境 ==="
  "$BOOT_PY" -m venv backend/venv
  if [ -x "backend/venv/bin/python" ]; then
    VENV_PY="backend/venv/bin/python"
  else
    VENV_PY="backend/venv/Scripts/python.exe"
  fi
fi
echo "=== backend: 用 $VENV_PY ==="

echo "=== backend: 安装依赖 ==="
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt

# ── 前端依赖 ──────────────────────────────────────────────
echo "=== frontend: 安装依赖（npm ci 走锁文件）==="
cd frontend
npm ci
