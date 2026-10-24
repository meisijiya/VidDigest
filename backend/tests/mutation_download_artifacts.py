"""变异脚本：证明「下载产物治理」那批测试真能杀掉缺陷（工单 #90）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。

## 为什么值得单独守

「用完即删」是一串**看不见**的约定：清理要挂在响应发完之后、失败路径
要自己清半截文件、巡检要按超龄兜底。这些约定写错了，功能测试可能照样
全绿——文件多留一会儿没人看见，0 字节响应要到用户那边才暴露。

所以只能靠「故意打坏 + 看它有没有变红」来证明守得住：测试写完不代表
它在守，只代表它在**绿**。

## 每条变异钉住一个具体后果

| 变异 | 现实里的后果 |
| --- | --- |
| M1 | 清理挂早一步，用户拿到 0 字节视频 |
| M2 | 文件永远留在盘上，磁盘慢慢被吃光 |
| M3 | 下载失败留下半截文件，占着地方没人管 |
| M4 | 上一台机器的残留被打进镜像 |
| M5 | 巡检对下载目录占用彻底失明 |
| M6 | yt-dlp 的半截文件认不出来，没人清 |
| M7 | 巡检在转码还没结束时把文件删了 |
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from venv_python import venv_python  # noqa: E402

# Windows 控制台默认不是 UTF-8，中文说明会变成乱码——这份输出是证据，
# 证据得能读。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
REPO = BACKEND.parent
PY = venv_python()
MAIN_FILE = REPO / "backend" / "main.py"
ARTIFACTS_FILE = REPO / "backend" / "download_artifacts.py"
DOCKERIGNORE_FILE = REPO / "backend" / ".dockerignore"
INSPECT_FILE = REPO / "scripts" / "ops" / "inspect_runtime.py"
TARGET = ["tests/test_download_artifacts.py", "tests/test_inspect_download_dir.py"]

#: (说明, 目标文件, 模式串, 替换串)
MUTATIONS = [
    (
        "M1 清理挂到了响应发出之前：用户拿到 0 字节视频",
        MAIN_FILE,
        "background_tasks.add_task(remove_artifact, filepath)",
        "remove_artifact(filepath)",
    ),
    (
        "M2 成功路径根本不删：产物永远留在盘上",
        MAIN_FILE,
        "background_tasks.add_task(remove_artifact, filepath)",
        "pass  # 变异：成功路径不再清理",
    ),
    (
        "M3 失败路径不清半截文件：网络断了就留下半个文件",
        MAIN_FILE,
        "cleanup_partial_artifacts(downloader.DOWNLOAD_DIR, started_at)",
        "cleanup_partial_artifacts(downloader.DOWNLOAD_DIR, started_at + 86400)",
    ),
    (
        "M4 构建上下文不再排除下载目录：残留被打进镜像",
        DOCKERIGNORE_FILE,
        "downloads/",
        "",
    ),
    (
        "M5 下载目录占用阈值变成 0：巡检对磁盘占用彻底失明",
        INSPECT_FILE,
        "DEFAULT_MAX_DOWNLOAD_MB = 2048",
        "DEFAULT_MAX_DOWNLOAD_MB = 0",
    ),
    (
        "M6 不再认 .part 半截文件：yt-dlp 的残留没人清",
        ARTIFACTS_FILE,
        'PARTIAL_SUFFIXES = (".part", ".ytdl")',
        'PARTIAL_SUFFIXES = (".tmp",)',
    ),
    (
        "M7 超龄下限被放开：巡检会在转码还没结束时删文件",
        INSPECT_FILE,
        "if hours < 1:",
        "if hours < 0:",
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
    print(f"基线 {len(TARGET)} 个测试文件: {summary}\n")

    originals = {
        MAIN_FILE: read_source(MAIN_FILE),
        ARTIFACTS_FILE: read_source(ARTIFACTS_FILE),
        DOCKERIGNORE_FILE: read_source(DOCKERIGNORE_FILE),
        INSPECT_FILE: read_source(INSPECT_FILE),
    }
    survived, not_run = [], []

    try:
        for name, target_file, pattern, replacement in MUTATIONS:
            original = originals[target_file]
            mutated = mutate_bytes(original, pattern, replacement)
            if mutated == original:
                print(f"  [未命中] {name} —— 模式没匹配上，跳过")
                not_run.append(name)
                continue
            write_source(target_file, mutated)
            try:
                _, out = run(TARGET)
            finally:
                write_source(target_file, original)

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
        for path, data in originals.items():
            write_source(path, data)

    for path, data in originals.items():
        if read_source(path) != data:
            print(f"\nFAIL: 还原后 {path.name} 与原始不一致（行尾符被改动）")
            return 9
    print("\n还原自检: main.py / download_artifacts.py / .dockerignore / inspect_runtime.py 逐字节一致")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 产物治理的测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，产物治理守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
