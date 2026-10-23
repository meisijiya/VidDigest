"""变异脚本：证明「老库升级」那批测试真能杀掉缺陷（工单 #68）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。

## 为什么单独一个脚本

``mutation_check.py`` 守的是「接缝与代际号」，``mutation_check_quota.py`` 守的是
「额度拆分那几列补齐没有」。它们都**没有**钉住同一件事：老库里**已经存在的那些
行**，在升级之后是不是还是原来那个意思。

这批测试守的正是那件事，所以另起一个脚本，而不是往上面两个里塞。

## 四个变异分别对应四类真实事故

  · M1 迁移根本不补 ``is_admin`` —— 老库升级后管理后台一读就 no such column。
  · M2 覆盖列补成 ``DEFAULT 0`` —— 0 的语义是「一条都不能用」，NULL 才是
    「回落全局上限」。补成 0 等于把每一个老账号静默封死。
  · M3 额度拆分那四列不补 —— 老库升级后一算额度就 no such column。
  · M4 ``is_admin`` 补成 ``DEFAULT 1`` —— 老账号因为升级**集体变成管理员**。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from venv_python import venv_python

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
PY = venv_python()
DB_FILE = BACKEND / "database.py"
TARGET = ["tests/test_users_legacy_upgrade.py"]

MUTATIONS = [
    (
        "M1 迁移不补 is_admin：老库升级后管理后台一读就 no such column",
        lambda s: mutate_bytes(
            s,
            '    if "is_admin" not in existing:\n'
            '        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")\n',
            '    if False:\n'
            '        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")\n',
        ),
    ),
    (
        "M2 覆盖列补成 DEFAULT 0：老账号被静默封死（0 ≠ 回落全局上限）",
        lambda s: mutate_bytes(
            s,
            '("parse_limit_override", "ALTER TABLE users ADD COLUMN parse_limit_override INTEGER"),',
            '("parse_limit_override", "ALTER TABLE users ADD COLUMN parse_limit_override INTEGER DEFAULT 0"),',
        ),
    ),
    (
        "M3 额度拆分那四列不补：老库升级后一算额度就 no such column",
        lambda s: mutate_bytes(
            s,
            '        ("last_chat_date", "ALTER TABLE users ADD COLUMN last_chat_date TEXT"),\n'
            "    ):\n"
            "        if column not in existing:\n"
            "            conn.execute(ddl)\n",
            '        ("last_chat_date", "ALTER TABLE users ADD COLUMN last_chat_date TEXT"),\n'
            "    ):\n"
            "        if False:\n"
            "            conn.execute(ddl)\n",
        ),
    ),
    (
        "M4 is_admin 补成 DEFAULT 1：老账号因为升级集体变成管理员",
        lambda s: mutate_bytes(
            s,
            'ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 1',
        ),
    ),
]

_COUNT_RE = re.compile(r"(\d+) (passed|failed|error)", re.IGNORECASE)


def read_source(path: Path):
    """按二进制读入，保留原始行尾符与末尾换行。

    用 text 模式读写会把 CRLF 规范化成 LF，脚本跑完文件就变了——
    工作树被污染，还原自检也会误判。
    """
    return path.read_bytes()


def write_source(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _loose_find(text: str, pattern: str):
    """在 text 里找 pattern，忽略空白差异，返回 (start, end) 或 None。

    非空白字符仍要逐字相同、顺序不变，只允许它们之间多出空白（换行、缩进、
    逗号后加空格……）。这些是格式化工具造成的样子，不是代码变了。

    刻意不用正则：把 \\s* 挂在每个字符后面会让回溯指数爆炸，源文件一大
    就能把进程卡死。这里是双指针扫描，复杂度 O(n*m)，没有回溯。
    """
    chars = [c for c in pattern if not c.isspace()]
    if not chars:
        return (0, 0) if not text.strip() else None
    n = len(text)
    for start in range(n):
        if text[start] != chars[0]:
            continue
        ti = start
        ok = True
        for c in chars:
            while ti < n and text[ti].isspace():
                ti += 1
            if ti >= n or text[ti] != c:
                ok = False
                break
            ti += 1
        if ok:
            return (start, ti)
    return None


def mutate_bytes(data: bytes, pattern: str, replacement: str = "") -> bytes:
    """在保持行尾符的前提下做替换，模式串里一律用 \\n 书写。

    源文件可能是 CRLF，模式串若直接按 \\n 匹配会全部落空——
    变异「没命中」看起来像「测试守不住」，其实是脚本没干活。
    这里先归一化行尾符，替换后再还原成原文件用的那一种。
    """
    crlf = b"\r\n" in data
    text = data.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    if pattern not in text:
        # 精确匹配落空时再试一次「空白宽松」：重排版不算代码变了。
        span = _loose_find(text, pattern)
        if span is None:
            return data  # 未命中：原样返回，让调用方识别
        text = text[: span[0]] + replacement + text[span[1] :]
    else:
        text = text.replace(pattern, replacement)
    if crlf:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


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


def main():
    code, out = run(TARGET)
    if code != 0:
        print("FAIL: 基线不绿，无法评估变异")
        print(out[-2000:])
        return 9
    p, f, summary = parse_counts(out)
    if p + f == 0:
        print("FAIL: 目标收集到 0 个测试，变异评估无意义")
        return 9
    print(f"基线 {TARGET[0]}: {summary}\n")

    original = read_source(DB_FILE)
    survived, not_run = [], []

    try:
        for name, mutate in MUTATIONS:
            mutated = mutate(original)
            if mutated == original:
                print(f"  [未命中] {name} —— 模式没匹配上，跳过")
                not_run.append(name)
                continue
            write_source(DB_FILE, mutated)
            try:
                _, out = run(TARGET)
            finally:
                write_source(DB_FILE, original)

            p, f, _ = parse_counts(out)
            if p + f == 0:
                print(f"  [没跑] {name} —— 收集到 0 个测试，结果无效")
                not_run.append(name)
            elif f > 0:
                print(f"  [杀死] {name}  ({f} 条转红)")
            else:
                print(f"  [存活] {name}  <-- 测试守不住")
                survived.append(name)
    finally:
        write_source(DB_FILE, original)

    # 自检：还原后必须与变异前逐字节相同，否则工作树被污染了。
    if read_source(DB_FILE) != original:
        print("\nFAIL: 还原后文件与原始不一致（行尾符被改动）")
        return 9
    print("\n还原自检: backend/database.py 逐字节一致")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 老库升级测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，老库升级路径守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
