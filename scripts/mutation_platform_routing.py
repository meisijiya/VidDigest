"""工单 #56 的变异 harness：证明平台分流真被钉住了。

四条变异必须 KILLED，一条反向对照必须保持绿。

判别力的来源：计数桩是**不对称**的吗？不是——两条支路都装了桩，
所以分流走反不会「对侧出网」，只会被计数直接抓到。这正是本单要补的那个缺口：
原有的隐含判别力取决于网络，在有网的机器上会变成慢红或假绿。
"""
import io
import subprocess
import sys

PY = "backend/venv/Scripts/python.exe"
TARGET = "backend/tests/test_download_video_error_paths.py"
MAIN = "backend/main.py"
DOUYIN = "backend/douyin.py"


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


PARSE_BRANCH = (
    "        if is_douyin_url(url):\n"
    "            result = await loop.run_in_executor(None, douyin_parser.parse, url)\n"
)
PARSE_ALWAYS = (
    "        if True:\n"
    "            result = await loop.run_in_executor(None, douyin_parser.parse, url)\n"
)
DL_BRANCH = (
    "        if is_douyin_url(url):\n"
    "            # 抖音这条路自己决定下什么，只认 mode 不认 format_id。不映射的话，\n"
)
DL_NEVER = (
    "        if False:\n"
    "            # 抖音这条路自己决定下什么，只认 mode 不认 format_id。不映射的话，\n"
)

MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "整张域名名单清空：抖音 URL 不再被识别",
        "find": '        "douyin.com", "iesdouyin.com", "v.douyin.com",\n'
                '        "www.douyin.com", "m.douyin.com",\n',
        "replace": '        "example.invalid",\n',
    },
    {
        "id": "M2", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "名单里只剩冗余的 iesdouyin.com（去掉 douyin.com）",
        "find": '        "douyin.com", "iesdouyin.com", "v.douyin.com",\n',
        "replace": '        "iesdouyin.com", "v.douyin.com",\n',
    },
    {
        "id": "M3", "expect": "KILLED", "file": MAIN, "hits": 1,
        "why": "/api/parse 分流恒真：非抖音 URL 也走抖音侧",
        "find": PARSE_BRANCH, "replace": PARSE_ALWAYS,
    },
    {
        "id": "M4", "expect": "KILLED", "file": MAIN, "hits": 1,
        "why": "/api/download 分流恒假：抖音 URL 也走 yt-dlp",
        "find": DL_BRANCH, "replace": DL_NEVER,
    },
    {
        "id": "R1", "expect": "GREEN", "file": MAIN, "hits": 1,
        "why": "反向对照：健康检查返回串与分流无关",
        "find": '    return {"status": "ok"}',
        "replace": '    return {"status": "healthy"}',
    },
]

bad = 0
cache = {}
for m in MUTATIONS:
    for key in ("find", "replace", "hits", "expect", "file"):
        if key not in m:
            print(f"[HARNESS ERROR] {m.get('id')} 缺 {key}——先修 harness，不要动断言")
            bad += 1
            break
    else:
        if m["file"] not in cache:
            cache[m["file"]] = read(m["file"])
        src = cache[m["file"]]
        hits = src.count(m["find"])
        if hits != m["hits"]:
            print(f"[PRECHECK BAD] {m['id']} anchor 命中 {hits} 次，预期 {m['hits']}"
                  f"——先问「是文件的问题还是我锚点的问题」")
            bad += 1
            continue
        write(m["file"], src.replace(m["find"], m["replace"], 1))
        code, out = run_tests()
        write(m["file"], src)

        verdict = "KILLED" if code != 0 else "GREEN"
        has_assertion = "AssertionError" in out
        ok = verdict == m["expect"] and (verdict != "KILLED" or has_assertion)
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {verdict} (期望 {m['expect']})"
              f"  AssertionError={'有' if has_assertion else '无'}  exit={code}"
              f"  — {m['why']}")
        if not ok:
            print(out[-700:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)