"""工单 #57 的变异 harness：证明新加的门禁自检真有判别力。

三条变异必须 KILLED，一条反向对照必须保持绿。目标文件是
``backend/tests/check_db_fixture.py`` 本身 —— 这是**源码级**变异，
不是模拟，所以「M1 真删掉那行过滤，上面那条断言会不会红」得到的是实测答案。
"""
import io
import subprocess
import sys

PY = "backend/venv/Scripts/python.exe"
TARGET = "backend/tests/test_db_fixture_gate.py"
GATE = "backend/tests/check_db_fixture.py"


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def eol_of(path):
    raw = open(path, "rb").read()
    return "\r\n" if raw.count(b"\r\n") else "\n"


def run_tests():
    proc = subprocess.run(
        [PY, "-m", "pytest", TARGET, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


NL = eol_of(GATE)


def n(*lines):
    return NL.join(lines)


MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "hits": 1,
        "why": "删掉 test_ 前缀过滤：报出来的变成 helper 自己的名字",
        "find": n('        if not node.name.startswith("test_"):',
                  "            continue",
                  "",
                  "        args = {a.arg for a in node.args.args}",
                  "        if _has_db_fixture(args, fixtures):"),
        "replace": n("        args = {a.arg for a in node.args.args}",
                     "        if _has_db_fixture(args, fixtures):"),
    },
    {
        "id": "M2", "expect": "KILLED", "hits": 1,
        "why": "_reachable_db 不再递归：helper 里的触库看不见了（工单 #57 的由来）",
        "find": n('    for child in funcs[name]["local"]:',
                  "        found |= _reachable_db(funcs, child, seen)"),
        "replace": n('    for child in funcs[name]["local"]:',
                     "        pass  # 变异：不递归"),
    },
    {
        "id": "M3", "expect": "KILLED", "hits": 1,
        "why": "_has_db_fixture 对直接参数视而不见：传递依赖解析失效",
        "find": n("    if args & DB_FIXTURE_ARGS:",
                  "        return True"),
        "replace": n("    if args & set():",
                     "        return True"),
    },
    {
        "id": "R1", "expect": "GREEN", "hits": 1,
        "why": "反向对照：文件头 docstring 改动与判定无关",
        "find": n('"""门禁：任何会触库的测试都必须取 db 夹具。'),
        "replace": n('"""门禁：任何会触库的测试都必须取 db 夹具（改）。'),
    },
]

bad = 0
src = read(GATE)
print(f"行尾：{NL!r}")
for m in MUTATIONS:
    for key in ("find", "replace", "hits", "expect"):
        if key not in m:
            print(f"[HARNESS ERROR] {m.get('id')} 缺 {key}——先修 harness，不要动断言")
            bad += 1
            break
    else:
        hits = src.count(m["find"])
        if hits != m["hits"]:
            print(f"[PRECHECK BAD] {m['id']} anchor 命中 {hits} 次，预期 {m['hits']}"
                  f"——先问「是文件的问题还是我锚点的问题」")
            bad += 1
            continue
        write(GATE, src.replace(m["find"], m["replace"], 1))
        code, out = run_tests()
        write(GATE, src)

        verdict = "KILLED" if code != 0 else "GREEN"
        has_assertion = "AssertionError" in out
        ok = verdict == m["expect"] and (verdict != "KILLED" or has_assertion)
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {verdict} (期望 {m['expect']})"
              f"  AssertionError={'有' if has_assertion else '无'}  exit={code}"
              f"  — {m['why']}")
        if not ok:
            print(out[-800:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)