"""工单 #56/#58 的变异 harness：证明平台分流真被钉住了。

十条变异必须 KILLED（清单里 ``expect: GREEN`` 的只有 R1），一条反向对照必须保持绿。

判别力的来源：计数桩是**不对称**吗？不是——两条支路都装了桩，
所以分流走反不会「对侧出网」，只会被计数直接抓到。这正是 #56 要补的那个缺口：
原有的隐含判别力取决于网络，在有网的机器上会变成慢红或假绿。

#58 把判定从子串匹配换成域名边界，锚点跟着换了一轮：域名名单从 5 条收到 2 条，
原先「删掉任意一条都转红」的那条性质现在**只对两条注册域成立**，所以 M2/M5 各钉一条，
另加 M6（退回子串匹配）与 R2/R3（判定恒真 / 恒假——工单验收 4 要求的「必须转红」，
方向与 R1 那条「必须保持绿」相反，故另起 id 而不复用 R1 的说法）。
M7/M8 钉的是第二个出口：``summarizer`` 的 ASR 取音频分流，
它与 ``main.py`` 的解析分流各走各的，钉住一个不等于钉住另一个。
"""
import io
import subprocess
import sys

PY = "backend/venv/Scripts/python.exe"
#: 平台分流有两个出口，两个都得在变异装置的射程内——只跑一个，
#: 另一个出口上的新出口就拿不到「结构被改坏时会红」这条确认。
TARGETS = (
    "backend/tests/test_download_video_error_paths.py",
    "backend/tests/test_asr_download_routing.py",
)
MAIN = "backend/main.py"
DOUYIN = "backend/douyin.py"
SUMMARIZER = "backend/summarizer.py"


def read(path):
    with io.open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def run_tests():
    proc = subprocess.run(
        [PY, "-m", "pytest", *TARGETS, "-q"],
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

DOMAIN_MATCH = (
    '        return any(host == d or host.endswith("." + d) for d in _DOUYIN_DOMAINS)'
)
ASR_BRANCH = "    if is_douyin_url(url):\n"
ASR_ALWAYS = "    if True:\n"
ASR_NEVER = "    if False:\n"

MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "整张域名名单清空：抖音 URL 不再被识别",
        "find": '_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")',
        "replace": '_DOUYIN_DOMAINS = ("example.invalid",)',
    },
    {
        "id": "M2", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "去掉 douyin.com（只剩 iesdouyin.com）：www.douyin.com 那几种形态失去依据",
        "find": '_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")',
        "replace": '_DOUYIN_DOMAINS = ("iesdouyin.com",)',
    },
    {
        "id": "M5", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "去掉 iesdouyin.com（只剩 douyin.com）：短链 302 到的终点失去依据",
        "find": '_DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")',
        "replace": '_DOUYIN_DOMAINS = ("douyin.com",)',
    },
    {
        "id": "M6", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "判定退回子串匹配：notdouyin.com 这类 host 又被判成抖音",
        "find": DOMAIN_MATCH,
        "replace": '        return any(d in host for d in _DOUYIN_DOMAINS)',
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
    {
        "id": "R2", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "判定恒真：B 站那几条用例必须转红（工单 #58 验收 4）",
        "find": DOMAIN_MATCH, "replace": "        return True",
    },
    {
        "id": "R3", "expect": "KILLED", "file": DOUYIN, "hits": 1,
        "why": "判定恒假：抖音那几条用例必须转红（工单 #58 验收 4）",
        "find": DOMAIN_MATCH, "replace": "        return False",
    },
    {
        "id": "M7", "expect": "KILLED", "file": SUMMARIZER, "hits": 1,
        "why": "ASR 取音频的分流恒真：近似 host 也走 DouyinParser",
        "find": ASR_BRANCH, "replace": ASR_ALWAYS,
    },
    {
        "id": "M8", "expect": "KILLED", "file": SUMMARIZER, "hits": 1,
        "why": "ASR 取音频的分流恒假：真实抖音链接掉回 yt-dlp",
        "find": ASR_BRANCH, "replace": ASR_NEVER,
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