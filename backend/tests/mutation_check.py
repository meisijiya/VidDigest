"""变异脚本：证明接缝的测试真能杀掉缺陷（一次性核验用，可重跑）。

逐个破坏 → 跑指定测试 → 必须转红。绿灯不算证据。
每个变异只改一个文件，跑完立即还原。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
PY = BACKEND / "venv" / "Scripts" / "python.exe"
DB_FILE = BACKEND / "database.py"
SEAMS_FILE = TESTS / "seams.py"
AUTH_FILE = BACKEND / "auth.py"
CONFTEST_FILE = TESTS / "conftest.py"
GUARD_FILE = TESTS / "check_db_fixture.py"

DB_MUTATIONS = [
    (
        "M1 代际号失效：清理时不递增（工作线程会复用旧库连接）",
        DB_FILE,
        lambda s: mutate_bytes(s, "        _conn_generation += 1\n"),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M2 代际号不参与判定：get_db 忽略 generation 变化",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            'if conn is not None and getattr(_thread_local, "generation", None) != generation:',
            "if False:",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M3 清理只递增代际号、不清连接登记（清单里堆满旧连接）",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "        _open_conns.clear()\n        _open_conns_threads.clear()\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M4 工作线程连接不登记：get_db 不记录创建线程",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "            _open_conns.append(conn)\n            _open_conns_threads.append(threading.get_ident())\n",
            "            _open_conns.append(conn)\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
]

SEAMS_MUTATIONS = [
    (
        "M5 桩退化为总计数：思维导图方法不计数（工单点名的原始缺陷）",
        SEAMS_FILE,
        lambda s: mutate_bytes(
            s,
            '    def generate_mindmap(self, text, language):\n'
            '        self._calls["generate_mindmap"] += 1\n',
            "    def generate_mindmap(self, text, language):\n",
        ),
        ["tests/test_seams.py", "-k", "TestStubCountsPerMethod"],
    ),
    (
        "M6 桩的 calls_of 忽略方法名，任何方法都返回总结次数（逐方法断言失效）",
        SEAMS_FILE,
        lambda s: mutate_bytes(
            s,
            '        if method not in self._calls:\n'
            '            raise KeyError(\n'
            '                f"未知的模型方法 {method!r}；可计数的：{sorted(self._calls)}"\n'
            '            )\n'
            '        return self._calls[method]',
            "        return self._calls.get('summarize_stream', 0)",
        ),
        ["tests/test_seams.py", "-k", "TestStubCountsPerMethod"],
    ),
    (
        "M7 桩的 calls_of 对未知方法返回 0 而非报错（拼错后静默通过）",
        SEAMS_FILE,
        lambda s: mutate_bytes(
            s,
            '        if method not in self._calls:\n'
            '            raise KeyError(\n'
            '                f"未知的模型方法 {method!r}；可计数的：{sorted(self._calls)}"\n'
            '            )\n'
            '        return self._calls[method]',
            "        return self._calls.get(method, 0)",
        ),
        ["tests/test_seams.py", "-k", "TestStubCountsPerMethod"],
    ),
    (
        "M10 主线程自己的连接也不 close（fd 泄漏，无人报警）",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "    for conn in owned_by_current:\n"
            "        try:\n"
            "            conn.close()\n"
            "        except sqlite3.Error:\n"
            "            pass\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M11 清理不记录 owned_by_current：主线程连接一条都不关",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "        owned_by_current = [\n"
            "            conn for conn, owner in zip(_open_conns, _open_conns_threads)\n"
            "            if owner == current\n"
            "        ]\n",
            "        owned_by_current = []\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M12 死线程条目不剔除：fd 随短命线程无界增长",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "    keep = [i for i, owner in enumerate(_open_conns_threads) if owner in alive]\n",
            "    keep = list(range(len(_open_conns_threads)))\n",
        ),
        ["tests/test_seams.py"],
    ),
    (
        "M13 夹具挂载期自检被删：跨测试隔离无人验证",
        CONFTEST_FILE,
        lambda s: mutate_bytes(
            s,
            "    if _previous_db[\"was_written\"]:\n",
            "    if False:\n",
        ),
        ["tests/test_seams.py", "-k", "TestFixtureWiringIsLoadBearing"],
    ),
    (
        "M14 夹具自检改在主线程读：泄漏查不出来但照样绿",
        CONFTEST_FILE,
        lambda s: mutate_bytes(
            s, "        leaked = run_in_worker(_read)\n", "        leaked = _read()\n",
        ),
        ["tests/test_seams.py", "-k", "TestFixtureWiringIsLoadBearing"],
    ),
    (
        "M15 夹具拆卸端不再清理：跨测试泄漏",
        CONFTEST_FILE,
        lambda s: mutate_bytes(
            s, "    yield database\n    close_all_thread_connections()\n",
            "    yield database\n",
        ),
        ["tests/test_seams.py", "-k", "TestFixtureWiringIsLoadBearing"],
    ),
    (
        "M16 门禁失效：scan() 恒返回空（允许不取 db 夹具）",
        GUARD_FILE,
        lambda s: mutate_bytes(
            s, "def scan() -> list:\n    all_offenders = []\n",
            "def scan() -> list:\n    return []\n    all_offenders = []\n",
        ),
        ["tests/test_db_fixture_guard.py"],
    ),
    (
        "M17 剔除快路径比数量而非身份：死线程条目被漏掉",
        DB_FILE,
        lambda s: mutate_bytes(
            s,
            "    if _open_conns_threads and alive.issuperset(_open_conns_threads):\n",
            "    if _open_conns_threads and len(alive) == len(_open_conns_threads):\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
    (
        "M18 主线程连接断言放宽回全部连接（含未关闭的 worker 连接）",
        TESTS / "test_seams.py",
        lambda s: mutate_bytes(
            s,
            "        mine = [conn for conn, owner in pairs if owner == current]\n",
            "        mine = [conn for conn, owner in pairs]\n",
        ),
        ["tests/test_seams.py", "-k", "TestWorkerThreadConnectionCleanup"],
    ),
]

AUTH_FILE = BACKEND / "auth.py"

# 破坏鉴权本身：若这些变异全被杀死，说明 HTTP 接缝真的在执行鉴权依赖，
# 而不是碰巧返回了预期状态码。
HTTP_MUTATIONS = [
    (
        "M8 受保护路由不再鉴权：get_current_user 无凭据也放行",
        AUTH_FILE,
        lambda s: mutate_bytes(
            s,
            '    if not credentials:\n'
            '        raise HTTPException(status_code=401, detail="请先登录")\n',
            '    if not credentials:\n'
            '        return {"id": 1, "email": "anon@example.com", "is_vip": 0,\n'
            '                "vip_expire_at": None, "daily_summary_count": 0}\n',
        ),
        ["tests/test_seams.py", "-k", "TestHttpSeamResolvesAuth"],
    ),
    (
        "M9 可选登录依赖恒返回 None：已登录也被当未登录",
        AUTH_FILE,
        lambda s: mutate_bytes(
            s,
            '        payload = decode_token(credentials.credentials)\n'
            '        from database import get_user_by_id\n'
            '        return get_user_by_id(payload["sub"])\n'
            '    except HTTPException:\n'
            '        return None\n',
            "        return None\n",
        ),
        ["tests/test_seams.py", "-k", "TestHttpSeamResolvesAuth"],
    ),
]

ALL_MUTATIONS = DB_MUTATIONS + SEAMS_MUTATIONS + HTTP_MUTATIONS

_COUNT_RE = re.compile(r"(\d+) (passed|failed|error)", re.IGNORECASE)


def read_source(path: Path):
    """按二进制读入，保留原始行尾符与末尾换行。

    用 text 模式读写会把 CRLF 规范化成 LF，脚本跑完文件就变了——
    git 会显示全文行尾符差异（实测 79 行增删），工作树被污染。
    """
    return path.read_bytes()


def write_source(path: Path, data: bytes) -> None:
    path.write_bytes(data)


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
        return data  # 未命中：原样返回，让调用方识别

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
    code, out = run(["tests"])
    if code != 0:
        print("FAIL: 基线不绿，无法评估变异")
        print(out[-2000:])
        return 9
    print(f"基线全量: {parse_counts(out)[2]}\n")

    # 每个过滤目标都要先确认能收集到测试，否则变异结果全是空的
    seen = set()
    for m in ALL_MUTATIONS:
        key = tuple(m[3])
        if key in seen:
            continue
        seen.add(key)
        code, out = run(list(key))
        p, f, summary = parse_counts(out)
        if p + f == 0:
            print(f"FAIL: 目标 {key} 收集到 0 个测试，变异评估无意义")
            print(out[-1200:])
            return 9
        print(f"目标 {' '.join(key)}: {p + f} 条可收集")
    print()

    originals = {f: read_source(f) for f in {m[1] for m in ALL_MUTATIONS}}
    survived, not_run = [], []

    try:
        for name, target_file, mutate, pytest_args in ALL_MUTATIONS:
            source = originals[target_file]
            mutated = mutate(source)
            if mutated == source:
                print(f"  [未命中] {name} —— 模式没匹配上，跳过")
                not_run.append(name)
                continue
            write_source(target_file, mutated)
            try:
                _, out = run(pytest_args)
            finally:
                write_source(target_file, source)

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
        for f, content in originals.items():
            write_source(f, content)

    # 自检：还原后必须与变异前逐字节相同，否则工作树被污染了。
    # 文件数是算出来的，不写死——之前写死成「三个」而实际有五个，
    # 正是这个脚本要防的那类「报告与实际不符」。
    dirty = [f.name for f, content in originals.items() if read_source(f) != content]
    if dirty:
        print(f"\nFAIL: 还原后文件与原始不一致（行尾符被改动）：{dirty}")
        return 9
    checked = len(originals)
    print(f"\n还原自检: {checked} 个文件均逐字节一致"
          f"（{', '.join(sorted(f.name for f in originals))}）")

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 接缝测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(ALL_MUTATIONS)} 个变异全部被杀死，三条接缝守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
