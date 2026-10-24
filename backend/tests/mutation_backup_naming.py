"""变异脚本：证明「备份落盘」那批测试真能杀掉缺陷（工单 #68）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。

## 为什么值得单独守

备份文件名原本精确到**秒**。一秒钟里跑两次备份是常事（定时任务与手动触发
撞在一起），两次落到同一个文件名就**互相覆盖**：看着备份了两份，实际只剩
一份，而且退出码还是 0。

这种缺陷的形状很特别——它不报错，只让「备份过」这件事慢慢变成谎话。所以
必须有一组断言专门盯它：磁盘上到底有几个文件、每个文件里是哪一刻的库。
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
REPO = BACKEND.parent
PY = venv_python()
BACKUP_FILE = REPO / "scripts" / "ops" / "backup_db.py"
TARGET = ["tests/test_ops_backup.py"]

MUTATIONS = [
    (
        "M1 退回修复前的写法：秒级时间戳且没有撞名序号（同一秒两次备份互相覆盖）",
        lambda s: mutate_bytes(
            s,
            '    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")\n'
            '    path = os.path.join(out_dir, "%s%s%s" % (PREFIX, stamp, SUFFIX))\n'
            "    seq = 2\n"
            "    while os.path.exists(path):\n"
            '        path = os.path.join(out_dir, "%s%s-%d%s" % (PREFIX, stamp, seq, SUFFIX))\n'
            "        seq += 1\n"
            "    return path\n",
            '    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")\n'
            '    return os.path.join(out_dir, "%s%s%s" % (PREFIX, stamp, SUFFIX))\n',
        ),
    ),
    (
        "M2 撞名序号去掉：微秒也撞时仍然互相覆盖",
        lambda s: mutate_bytes(
            s,
            "    seq = 2\n    while os.path.exists(path):\n",
            "    seq = 2\n    while False:\n",
        ),
    ),
    (
        "M3 微秒去掉：只剩秒级，文件名分不出先后，保留策略跟着失准",
        lambda s: mutate_bytes(
            s,
            '    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")\n',
            '    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")\n',
        ),
    ),
]

_COUNT_RE = re.compile(r"(\d+) (passed|failed|error)", re.IGNORECASE)


def read_source(path: Path):
    """按二进制读入，保留原始行尾符与末尾换行。"""
    return path.read_bytes()


def write_source(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _loose_find(text: str, pattern: str):
    """在 text 里找 pattern，忽略空白差异，返回 (start, end) 或 None。

    非空白字符仍要逐字相同、顺序不变，只允许它们之间多出空白。
    刻意不用正则：把 \\s* 挂在每个字符后面会让回溯指数爆炸。
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
    """在保持行尾符的前提下做替换，模式串里一律用 \\n 书写。"""
    crlf = b"\r\n" in data
    text = data.decode("utf-8")
    if crlf:
        text = text.replace("\r\n", "\n")

    if pattern not in text:
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

    original = read_source(BACKUP_FILE)
    survived, not_run = [], []

    try:
        for name, mutate in MUTATIONS:
            mutated = mutate(original)
            if mutated == original:
                print(f"  [未命中] {name} —— 模式没匹配上，跳过")
                not_run.append(name)
                continue
            write_source(BACKUP_FILE, mutated)
            try:
                _, out = run(TARGET)
            finally:
                write_source(BACKUP_FILE, original)

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
        write_source(BACKUP_FILE, original)

    if read_source(BACKUP_FILE) != original:
        print("\nFAIL: 还原后文件与原始不一致（行尾符被改动）")
        return 9
    print("\n还原自检: scripts/ops/backup_db.py 逐字节一致")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 备份落盘测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，备份落盘行为守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
