#!/usr/bin/env python
"""门禁口径一致性：文档里写的关卡数必须等于 init.sh 真跑的关卡数。

**为什么这道关卡存在**：AGENTS.md 曾经把关卡数写少一关，而 init.sh 早就是五关了。
本仓库的 AGENTS.md 是给下一个 agent 读的**指令文件**，
指令文件里的过期数字会被**执行**，不是被阅读——它错了没人报警。
本轮就抓到一个活的：init.sh 里敏感内容扫描那段注释自报「第 4 道关卡」，
而它实际是第 3 关。

**真值只有一个**：init.sh 里实际跑了几关。这个脚本从 init.sh 数，**不硬编码数字**。

**查三件事**：

1. init.sh 里每个关卡块自报的「第 N 关」必须等于它自己的序号
   （关卡 3 自称第 4 关 = 腐烂；编号对但描述对不上另说）。
2. 跟踪文档里「跑 N 关 / N 件 / N 道」这种**总数**表述必须等于真实关卡数。
3. 跟踪文档里「第 N 关」这种**编号引用**不能超过真实关卡数。
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

INIT = os.path.join(REPO, "init.sh")

# init.sh 里每真跑一关就加一次计数
RAN_RE = re.compile(r"RAN=\$\(\(RAN\s*\+\s*1\)\)")

# 中文数字
CN_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 总数表述：关键词 + 数字 + 关/道/件
TOTAL_RE = re.compile(
    r"(?:跑|共|计|依次|共有|总共|合计|共计|一共)\s*"
    r"([0-9]+|[一二两三四五六七八九十]+)\s*(?:道|关|件)"
)

# 编号引用：第 N 关 / 第 N、Y 关
ENUM_RE = re.compile(r"第\s*([0-9]+(?:\s*[、,，]\s*[0-9]+)*)\s*[关道]")


def to_int(token):
    token = token.strip()
    if token.isdigit():
        return int(token)
    return CN_NUM.get(token)


def tracked_files():
    proc = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write("ERROR: git ls-files 失败，无法确定检查范围。\n")
        sys.exit(2)
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def read(rel):
    with open(os.path.join(REPO, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    if not os.path.isfile(INIT):
        sys.stderr.write("ERROR: 找不到 init.sh，无法确定门禁有几关。\n")
        sys.exit(2)

    init_text = read("init.sh")
    blocks = init_text.split("RAN=$((RAN + 1))")
    total = len(blocks) - 1
    if total <= 0:
        sys.stderr.write("ERROR: 从 init.sh 里数不出任何关卡，门禁本身坏了。\n")
        sys.exit(2)

    violations = []

    # 1) init.sh 每个关卡块自报的序号
    for index, block in enumerate(blocks[:-1], 1):
        for m in ENUM_RE.finditer(block):
            for token in re.findall(r"\d+", m.group(1)):
                if int(token) != index:
                    violations.append(
                        "init.sh: 第 %s 关的注释写在第 %s 关的块里（块序号 %d）"
                        % (token, token, index)
                    )

    # 2) 文档里的总数表述与编号引用
    for rel in tracked_files():
        if not rel.lower().endswith((".md", ".sh")):
            continue
        if rel == "init.sh":
            continue
        for num, line in enumerate(read(rel).splitlines(), 1):
            for m in TOTAL_RE.finditer(line):
                value = to_int(m.group(1))
                if value is None:
                    continue
                if value != total:
                    violations.append(
                        "%s:%d: 门禁关卡数写的是 %s，init.sh 实际是 %d"
                        % (rel, num, m.group(0), total)
                    )
            for m in ENUM_RE.finditer(line):
                for token in re.findall(r"\d+", m.group(1)):
                    if int(token) > total:
                        violations.append(
                            "%s:%d: 引用了第 %s 关，但门禁只有 %d 关"
                            % (rel, num, token, total)
                        )

    if violations:
        print("gate: %d 处口径不一致（init.sh 实际 %d 关）" % (len(violations), total))
        for v in violations:
            print("  " + v)
        sys.exit(1)

    print("gate: OK（init.sh %d 关，文档口径一致）" % total)


if __name__ == "__main__":
    main()
