"""变异脚本：证明「公开端点并发闸门」那批测试真能杀掉缺陷（工单 #89）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。

## 为什么值得单独守

并发闸门是**没有症状**的防护：把 `429` 变成 `200`、把名额的归还语句删掉、
让两个端点桶共用一个计数键，功能测试全绿，而服务在真实流量下会被打穿。

这类缺陷只能靠「故意打坏 + 看它有没有变红」来证明守卫有效：
测试写完不代表它在守，只代表它在**绿**。

## 变异清单对应哪条用户故事

- M1 阈值 2→99：默认护栏被放宽（故事 11/12 的默认值那一条）
- M2 去掉异常回落：任务结束不还名额，一次崩溃就把那个来源锁在门外（故事 6/7）
- M3 摘掉解析端点上的并发依赖：重活又没人管（故事 1/2）
- M4 两个端点桶共用一个计数键：解析把下载也堵死（故事 3）
- M5 忽略阈值环境变量：换配置也没用（故事 11）
- M6 下载端点不主动还名额：名额要等响应发完才还回去，客户端在响应体刚
  开始发的时候再点一次会被自己上一个任务挡住
- M7 并发 429 的错误体没有 `code`：前端退回靠中文文案认自家闸门，
  两边分头改就会把并发闸门误报成平台风控

## 回归

脚本最后跑一遍**全量**后端用例：这次改动不能把既有用例弄坏。
前端的回归在 `init.sh` 的 `npm test` 那一关。
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
TARGET = ["tests/test_concurrency_limit.py"]

#: (说明, 目标文件, 模式串, 替换串)
MUTATIONS = [
    (
        "M1 默认阈值从 2 改成 99：护栏形同虚设",
        RATELIMIT_FILE,
        '    "/api/parse": ("VIDDIGEST_CONCURRENCY_PARSE", 2),\n',
        '    "/api/parse": ("VIDDIGEST_CONCURRENCY_PARSE", 99),\n',
    ),
    (
        "M2 任务结束不还名额：一次崩溃就把自己锁在门外",
        RATELIMIT_FILE,
        "            self._counter.release(self._key)\n",
        "            pass\n",
    ),
    (
        "M3 解析端点摘掉并发依赖：重活又没人管了",
        MAIN_FILE,
        'async def parse_video(req: ParseRequest, _rl: None = Depends(rate_limit("/api/parse")), _slot=Depends(concurrency_limit("/api/parse"))):',
        'async def parse_video(req: ParseRequest, _rl: None = Depends(rate_limit("/api/parse"))):',
    ),
    (
        "M4 两个端点桶共用一个计数键：解析把下载也堵死",
        RATELIMIT_FILE,
        "        key = (client_identity(request), bucket)\n",
        '        key = (client_identity(request), "")\n',
    ),
    (
        "M5 环境变量改不动并发上限：换配置也没用",
        RATELIMIT_FILE,
        "    env_name, default = CONCURRENCY_BUCKETS[bucket]\n"
        "    return _env_int(env_name, default)\n",
        "    env_name, default = CONCURRENCY_BUCKETS[bucket]\n"
        "    return default\n",
    ),
    (
        "M6 下载端点不主动还名额：名额要等响应发完才还回去",
        MAIN_FILE,
        "        # 名额必须在响应发出去之前还回去：文件已经在盘上了，剩下只是回传，\n"
        "        # 那不该继续占着名额（客户端收到响应后可能立刻再发一次）。\n"
        "        _slot.release()\n",
        "        # 名额必须在响应发出去之前还回去：文件已经在盘上了，剩下只是回传，\n"
        "        # 那不该继续占着名额（客户端收到响应后可能立刻再发一次）。\n"
        "        pass  # 变异：名额不还了\n",
    ),
    (
        "M7 并发 429 的错误体没有 code：前端只能去认中文文案",
        RATELIMIT_FILE,
        '                    "code": CONCURRENCY_LIMIT_CODE,\n',
        "",
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
    failed = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) == "failed")
    errors = sum(int(m.group(1)) for m in _COUNT_RE.finditer(summary) if m.group(2) == "error")
    return passed, failed, errors, summary.strip()


#: 一次「像样的」转红长什么样：断言红了（AssertionError），或者
#: pytest.raises 那个守卫没被触发（DID NOT RAISE）。这两种都说明
#: **判据在守**。收集期的错误（语法错、导入错）不算——那说明的是
#: 「我改坏了代码」。
_ASSERTION_MARKERS = ("AssertionError", "DID NOT RAISE")


def main():
    code, out = run(TARGET)
    if code != 0:
        print("FAIL: 基线不绿，无法评估变异")
        print(out[-2000:])
        return 9
    p, f, e, summary = parse_counts(out)
    if p + f + e == 0:
        print("FAIL: 目标收集到 0 个测试，变异评估无意义")
        return 9
    print(f"基线 {TARGET[0]}: {summary}\n")

    originals = {
        RATELIMIT_FILE: read_source(RATELIMIT_FILE),
        MAIN_FILE: read_source(MAIN_FILE),
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

            p, f, e, _ = parse_counts(out)
            if p + f + e == 0:
                print(f"  [没跑] {name} —— 收集到 0 个测试，结果无效")
                not_run.append(name)
            elif f == 0:
                print(f"  [存活] {name}  <-- 测试守不住")
                survived.append(name)
            elif e > 0:
                # 收集期就炸了（语法错、导入错）：那证明的是「我改坏了
                # 代码」，不是「判据在守」。
                print(f"  [无效] {name}  <-- 收集期报错（{e} 个 error），不是判据转红")
                not_run.append(name)
            elif not any(marker in out for marker in _ASSERTION_MARKERS):
                print(f"  [无效] {name}  <-- 红了但不像断言转红")
                not_run.append(name)
            else:
                print(f"  [杀死] {name}  ({f} 条转红，伴随断言)")
    finally:
        for path, data in originals.items():
            write_source(path, data)

    for path, data in originals.items():
        if read_source(path) != data:
            print(f"\nFAIL: 还原后 {path.name} 与原始不一致（行尾符被改动）")
            return 9
    print("\n还原自检: ratelimit.py / main.py 逐字节一致")

    # ── 回归：这次改动不能把既有用例弄坏 ──
    code, out = run(["tests"])
    if code != 0:
        print("\nFAIL: 回归不绿 —— 既有后端用例被这次改动弄坏了")
        print(out[-3000:])
        return 9
    _p, _f, _e, summary = parse_counts(out)
    print(f"回归: 全量后端用例 {summary}")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 并发闸门测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，并发闸门行为守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
