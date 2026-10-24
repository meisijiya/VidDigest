"""变异脚本：证明「公开端点限流」那批测试真能杀掉缺陷（工单 #85）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。

## 为什么值得单独守

限流是**没有症状**的防护：把 `429` 改成 `200`、把 `Retry-After` 去掉、
把来源判据放松，功能测试全绿，而服务在真实流量下会被打穿。

这类缺陷只能靠「故意打坏 + 看它有没有变红」来证明守卫有效：
测试写完不代表它在守，只代表它在**绿**。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from venv_python import venv_python  # noqa: E402

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
REPO = BACKEND.parent
PY = venv_python()
RATELIMIT_FILE = REPO / "backend" / "ratelimit.py"
MAIN_FILE = REPO / "backend" / "main.py"
AUTH_FILE = REPO / "backend" / "api_auth.py"
TARGET = ["tests/test_ratelimit.py"]

#: (说明, 目标文件, 模式串, 替换串)
MUTATIONS = [
    (
        "M1 限流不拦了：429 变成 200",
        RATELIMIT_FILE,
        "        status_code=429,\n",
        "        status_code=200,\n",
    ),
    (
        "M2 客户端拿不到 Retry-After：客户端不知道该等多久",
        RATELIMIT_FILE,
        '        headers={"Retry-After": str(retry_after)},\n',
        "        headers={},\n",
    ),
    (
        "M3 无条件采信 X-Forwarded-For：改个头就绕开限流",
        RATELIMIT_FILE,
        "    if _is_trusted_peer(peer):\n",
        "    if True:\n",
    ),
    (
        "M4 计数表不设上限：换着 IP 打就能把内存吃光",
        RATELIMIT_FILE,
        "                if len(self._entries) >= self._max_keys:\n"
        "                    self._evict_oldest()\n",
        "",
    ),
    (
        "M5 解析端点摘掉限流依赖",
        MAIN_FILE,
        'async def parse_video(req: ParseRequest, _rl: None = Depends(rate_limit("/api/parse"))):',
        "async def parse_video(req: ParseRequest):",
    ),
    (
        "M6 环境变量改不动上限：换配置也没用",
        RATELIMIT_FILE,
        "    env_name, default = BUCKETS[bucket]\n"
        "    return _env_int(env_name, default)\n",
        "    env_name, default = BUCKETS[bucket]\n"
        "    return default\n",
    ),
    (
        "M7 登录端点摘掉限流依赖：撞库面没护栏",
        AUTH_FILE,
        'async def login(req: LoginRequest, _rl: None = Depends(rate_limit("/api/login"))):',
        "async def login(req: LoginRequest):",
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

    originals = {
        RATELIMIT_FILE: read_source(RATELIMIT_FILE),
        MAIN_FILE: read_source(MAIN_FILE),
        AUTH_FILE: read_source(AUTH_FILE),
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
    print("\n还原自检: ratelimit.py / main.py / api_auth.py 逐字节一致")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 限流测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，限流行为守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
