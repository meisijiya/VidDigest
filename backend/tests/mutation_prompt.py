"""变异脚本：证明「总结正文不许写短」那组用例真在验（可重跑）。

改提示词很容易，改完门禁照样绿——因为没有任何一条测试会因为
「提示词又变回只讲格式」而转红。这份脚本逐个破坏 → 跑指定测试 →
必须转红。绿灯不算证据。

沿用 mutation_check.py 的两处纪律：
  1. 变异前先确认目标能收集到测试（pytest 的 exit 5 是「一条没选中」，
     不是「测试失败」；用退出码判存活会把空变异伪装成护栏有效）；
  2. 替换时归一化行尾符——summarizer.py 是 CRLF，模式串按 \\n 写，
     不归一就一个字符都匹配不上，「没命中」长得和「守不住」一模一样。

每个变异跑完立刻还原，绝不留下变异体。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
PY = BACKEND / "venv" / "Scripts" / "python.exe"
PROMPT_FILE = BACKEND / "summarizer.py"

TARGET = ["tests/test_summarize_routes.py", "-k", "TestSummaryPromptContentDemand"]

_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|error)")


def read_source(path: Path) -> bytes:
    return path.read_bytes()


def write_source(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def mutate_bytes(data: bytes, pairs) -> bytes:
    """在保持行尾符的前提下做替换，模式串里一律用 \\n 书写。"""
    crlf = b"\r\n" in data
    text = data.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    for pattern, replacement in pairs:
        if pattern not in text:
            return data  # 未命中：原样返回，让调用方识别
        text = text.replace(pattern, replacement)

    if crlf:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


# 越障与脱困 / 尘盒与维护 两节，各 3 条要点
_BLOCK_YUEZHANG = (
    "- 门槛测试设了 1.5cm 和 2cm 两档：只有甲机能过 2cm，乙机和丙机都卡在 1.5cm\n"
    "- 卡住之后三款行为差别很大：甲机会减速换个角度再试，其余两款直接停在原地\n"
    "- 地毯边缘的脱困测试里乙机成功率最低，重复三次里有一次彻底没出来\n"
)
_BLOCK_CHENHE = (
    "- 甲机尘盒可水洗，清理时不必反复倒灰，这是它日常体验加分最多的一点\n"
    "- 乙机的尘盒滤网网孔偏密，两周后堵得比预想快，每次清理都得拿刷子挑\n"
    "- 丙机尘盒是密封设计，不挑灰但不能水洗，长期发霉的风险要自己承担\n"
)

MUTATIONS = [
    (
        "P1 篇幅下限被改成「尽量详实」（= 没有下限）",
        [(r"7. **篇幅**：正文不少于 1500 字（不含标题行）。",
          "7. **篇幅**：正文尽量详实。")],
    ),
    (
        "P2 示例退回占位骨架「（总结正文……）」",
        [("- 三款定价依次为 2199、1299、899 元，差价主要来自导航方案和尘盒结构",
          "（总结正文……）")],
    ),
    (
        "P3 示例要点被削到 9 条（演示不出密度）",
        [(_BLOCK_YUEZHANG, ""), (_BLOCK_CHENHE, "")],
    ),
    (
        "P4 导图字数限制去掉作用域隔离（会泛化成全文基调）",
        [("（下面的长度与节点数限制**只适用于思维导图**，不要套用到第一部分的总结正文。\n"
          "导图刻意精简、总结正文刻意详实，这是两件不同的事。）",
          "（导图按下面的要求输出。）")],
    ),
    (
        "P5 标签数量限制去掉作用域隔离",
        [("（同样只约束标签本身，不影响前两部分。）", "")],
    ),
    (
        "P6 「覆盖优先 / 不要编造」被软化",
        [("**覆盖优先**", "**自由取舍**"), ("不要编造", "可以补充")],
    ),
    (
        "P7 正反例对照被删（只说「要有内容」不够）",
        [("   - 反例：`成本分析：不同模型的 API 费用对比`（只说了这节要讲什么，没有内容）\n"
          "   - 反例：`做法介绍：介绍这道菜的基本做法`（同上）\n"
          "   - 正例：`做法介绍：先用盐水浸泡 20 分钟去腥，两面煎至金黄后转小火慢炖 40 分钟`\n"
          "   - 正例：`模型表现：横测里它在代码任务通过率最高，但长上下文出现了明显退化`\n",
          "")],
    ),
    (
        # 真变异：不是删标题（那只是排版，需求还在），是把整块内容规则挪到
        # 第二部分之后 —— 作用域错位，模型会把「覆盖优先 / 1500 字」读成
        # 导图或标签的要求。
        "P8 内容要求被挪到第二部分之后（作用域错位）",
        [
            (
                "二、内容（比上面四条格式更重要，务必遵守）\n"
                "5. 这是**摘要**，不是目录。每一条要点都必须写出实质内容——具体的事实、数据、\n"
                "   结论、观点，或它和其它要点的因果关系。**严禁**只把小标题换个说法：\n"
                "   - 反例：`成本分析：不同模型的 API 费用对比`（只说了这节要讲什么，没有内容）\n"
                "   - 反例：`做法介绍：介绍这道菜的基本做法`（同上）\n"
                "   - 正例：`做法介绍：先用盐水浸泡 20 分钟去腥，两面煎至金黄后转小火慢炖 40 分钟`\n"
                "   - 正例：`模型表现：横测里它在代码任务通过率最高，但长上下文出现了明显退化`\n"
                "6. **覆盖优先**：字幕里出现过的每一个主要话题都要讲到，不要因为「看起来不重要」\n"
                "   就略过；拿不准要不要展开的次要话题，也用一句话把它带过。\n"
                "7. **篇幅**：正文不少于 1500 字（不含标题行）。宁可把一个话题的背景和因果讲透，\n"
                "   也不要列一堆小标题就收尾；同时不要用重复的话凑字数。\n"
                "8. 字幕里没有的信息不要编造。听不清、字幕本身就没展开的部分，直接略过。\n\n"
                "【第二部分：思维导图】",
                "【第二部分：思维导图】",
            ),
            (
                "【第三部分：标签】",
                "二、内容（比上面四条格式更重要，务必遵守）\n"
                "5. 这是**摘要**，不是目录。每一条要点都必须写出实质内容。\n"
                "6. **覆盖优先**：字幕里出现过的每一个主要话题都要讲到。\n"
                "7. **篇幅**：正文不少于 1500 字（不含标题行）。\n"
                "8. 字幕里没有的信息不要编造。\n\n"
                "【第三部分：标签】",
            ),
        ],
    ),
]


def run(args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [str(PY), "-m", "pytest", *args, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=BACKEND, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return r.returncode, r.stdout


def parse_counts(out):
    lines = out.strip().splitlines()
    summary = lines[-1] if lines else out
    passed = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) == "passed")
    failed = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) in ("failed", "error"))
    return passed, failed, summary.strip()


def failed_names(out):
    return re.findall(r"(?:FAILED|_+ Test\S+)", out)


def main():
    code, out = run(["tests/test_summarize_routes.py"])
    if code != 0:
        print("FAIL: 基线不绿，无法评估变异")
        print(out[-2000:])
        return 9
    print(f"基线: {parse_counts(out)[2]}")

    code, out = run(list(TARGET))
    p, f, summary = parse_counts(out)
    if p + f == 0:
        print(f"FAIL: 目标 {TARGET} 收集到 0 个测试，变异评估无意义")
        print(out[-1200:])
        return 9
    print(f"目标收集: {p + f} 条\n")

    original = read_source(PROMPT_FILE)
    killed, survived, not_run = 0, [], []

    try:
        for name, pairs in MUTATIONS:
            mutated = mutate_bytes(original, pairs)
            if mutated == original:
                print(f"  [未命中] {name} —— 模式没匹配上，先更新脚本，别跳过")
                not_run.append(name)
                continue
            write_source(PROMPT_FILE, mutated)
            try:
                code, out = run(list(TARGET))
                _, failed, summary = parse_counts(out)
                if failed == 0:
                    survived.append(f"{name} —— 存活（{summary}，这组用例没在验它）")
                else:
                    killed += 1
                    names = failed_names(out)
                    print(f"KILLED  {name}")
                    print(f"        转红于: {', '.join(n.strip('_ ') for n in names[:4])}")
            finally:
                write_source(PROMPT_FILE, original)
    finally:
        write_source(PROMPT_FILE, original)

    print("=" * 72)
    print(f"变异结果：{killed}/{len(MUTATIONS)} 杀，"
          f"{len(survived)} 存活，{len(not_run)} 未命中")
    for s in survived:
        print("  存活: " + s)
    for s in not_run:
        print("  未命中: " + s)
    return 1 if (survived or not_run) else 0


if __name__ == "__main__":
    sys.exit(main())
