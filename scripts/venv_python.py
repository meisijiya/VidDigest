"""解析 backend 虚拟环境里的解释器：Windows 与 Linux/macOS 各一条路。

**为什么要有它**：本仓库的解释器路径曾被写死成
`backend/venv/Scripts/python.exe`——那是 Windows 的一条路。写死后，
Linux/macOS 上 clone 下来的仓库连门禁都跑不起来，CI 也就无从谈起：
**一个只能在作者本人机器上跑的门禁，等于没有门禁。**

**它做什么**：按存在性挑一条能用的路径。挑不到就报错退出——
不猜，也不回退到系统 Python（系统 Python 的版本不对，
AGENTS.md「环境事实」一节记的就是这件事）。

**为什么单独成文件**：门禁、10 个变异 harness、bootstrap 脚本都要这条路径。
复制粘贴十份，抄错的概率就是下一次「跑不起来」的原因。
"""

import os

# 仓库根：本文件在 <repo>/scripts/ 下。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 候选顺序：先 Windows，再 POSIX。同一台机器上通常只有一条存在。
CANDIDATES = (
    ("backend", "venv", "Scripts", "python.exe"),
    ("backend", "venv", "bin", "python"),
)


def venv_python(repo=REPO):
    """返回 backend 虚拟环境解释器的路径；找不到就退出。"""
    for parts in CANDIDATES:
        path = os.path.join(repo, *parts)
        if os.path.isfile(path):
            return path
    tried = "、".join(os.path.join(repo, *parts) for parts in CANDIDATES)
    raise SystemExit(
        "ERROR: 找不到 backend 虚拟环境里的解释器（试过：%s）。\n"
        "先跑 scripts/bootstrap.sh 建好环境，再重跑。" % tried
    )


if __name__ == "__main__":
    print(venv_python())
