"""工单 #52 的变异 harness：证明 clean_url 的字符集真的被钉住了。

三条变异必须 KILLED，一条反向对照必须保持绿。

**本单与 #51 那次不同的地方**：字符集那条变异在 `main.py` 里出现**两次**
（`ParseRequest.clean_url` 与 `DownloadRequest.clean_url` 逐字相同），
所以 `expect_hits` 不是 1 而是 2。harness 支持按条指定，
把「命中数不等于预期」当成预检失败而不是「变异没生效」。

收尾一律 `git diff --stat` 自查——还原命令自己也会坏。
"""
import io
import subprocess
import sys

from venv_python import venv_python

PY = venv_python()
TARGET = "backend/tests/test_clean_url_contract.py"
MAIN = "backend/main.py"


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run_tests():
    proc = subprocess.run(
        [PY, "-m", "pytest", TARGET, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


#: 字符集原文（两处逐字相同）。用 raw 串，避免转义层数多一层就坏。
FULL_CLASS = r'''[^\s）\)\"\'＞，。、；：！？》>\]]+'''
NO_CJK = r'''[^\s）\)\"\'>\]]+'''
NO_QUOTE = r'''[^\s）\)\"＞，。、；：！？》>\]]+'''
ANCHOR = 're.search(r"https?://' + FULL_CLASS + '", self.url)'

FALLBACK_FROM = "        return match.group(0) if match else self.url.strip()"
FALLBACK_TO = "        return match.group(0) if match else self.url"

HEALTH_FROM = '    return {"status": "ok"}'
HEALTH_TO = '    return {"status": "healthy"}'

MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "expect_hits": 2,
        "why": "删掉字符集里排除中文标点的那 10 个字符（工单 #52 的验收核心）",
        "find": ANCHOR, "replace": 're.search(r"https?://' + NO_CJK + '", self.url)',
    },
    {
        "id": "M2", "expect": "KILLED", "expect_hits": 2,
        "why": "退化分支不再 strip()",
        "find": FALLBACK_FROM, "replace": FALLBACK_TO,
    },
    {
        "id": "M3", "expect": "KILLED", "expect_hits": 2,
        "why": "字符集里排除半角单引号",
        "find": ANCHOR, "replace": 're.search(r"https?://' + NO_QUOTE + '", self.url)',
    },
    {
        "id": "R1", "expect": "GREEN", "expect_hits": 1,
        "why": "反向对照：健康检查的返回串与 clean_url 无关，必须保持绿",
        "find": HEALTH_FROM, "replace": HEALTH_TO,
    },
]

bad = 0
src = read(MAIN)
for m in MUTATIONS:
    if "replace" not in m or "find" not in m:
        print(f"[HARNESS ERROR] {m['id']} 定义不完整——先修 harness，不要动断言")
        bad += 1
        continue
    hits = src.count(m["find"])
    if hits != m["expect_hits"]:
        print(f"[PRECHECK BAD] {m['id']} anchor 命中 {hits} 次，预期 {m['expect_hits']}"
              f"——先问「是文件的问题还是我锚点的问题」")
        bad += 1
        continue
    write(MAIN, src.replace(m["find"], m["replace"]))
    code, out = run_tests()
    write(MAIN, src)

    verdict = "KILLED" if code != 0 else "GREEN"
    has_assertion = "AssertionError" in out
    ok = verdict == m["expect"] and (verdict != "KILLED" or has_assertion)
    if not ok:
        bad += 1
    print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {verdict} (期望 {m['expect']})"
          f"  AssertionError={'有' if has_assertion else '无'}  exit={code}"
          f"  — {m['why']}")
    if not ok and verdict == "KILLED":
        print(out[-700:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)