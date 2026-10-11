"""工单 #59 的变异 harness：抖音那一份 host 名单真被钉住了吗。

每条变异都在钉一件具体的事，期望值就是判据本身：
C2/C3 钉形态矩阵里那两条裸域形态（去掉裸域条目就没人认它们），
C4 钉 host 闸门本身（path 长得像抖音视频不算数），
C1/C7 钉 iesdouyin 那种分享形态，C5 钉判定整体失效。

## 口径

跑的是 `test_url_canonical.py` 单文件，不是全量：本装置的每条变异都改
`url_canonical.py`，而这个文件是它的行为契约本身；单文件全红时全量必然全红，
反过来（单文件绿而全量红）才是要查的情况。单文件口径不比全量松，且快得多。
唯一需要全量口径才能下结论的是「等价改写」（C6）——那次跑过全量，绿，
所以它才能以 `expect: GREEN` 记在这里：哪天它转红，说明两种写法真的分道了，
那时改的是这条记录，不是断言。

判别力的来源：断言的是 `canonical_video_url()` 的返回值，改名单与改判定都直接改它。
不联网：这个文件全是纯函数断言，变异跑得快，也不受网络状态影响。
"""
import io
import re
import subprocess
import sys

PY = "backend/venv/Scripts/python.exe"
TARGET = "backend/tests/test_url_canonical.py"
CANON = "backend/url_canonical.py"

#: pytest ``-q`` 末行摘要：``3 passed`` / ``1 failed, 12 passed``。
#: exit 5 = 一条都没选中，**不等于**测试失败（TESTING-STRATEGY §6.2），
#: 判成 KILLED 就是把「一条都没跑」报成「守住了」，那是最坏的假绿。
_COUNT = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed)")


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
    out = (proc.stdout or "") + (proc.stderr or "")
    ran = sum(int(n) for n, _ in _COUNT.findall(out))
    return proc.returncode, out, ran


HOSTS = ('_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", '
         '"iesdouyin.com", "www.iesdouyin.com"}')
BRANCH = "    if host in _DOUYIN_HOSTS:"

#: 换成子域判定（``douyin._DOUYIN_DOMAINS`` 那种写法）。对用户真会粘的形态
#: **等价**——所以它必须保持绿。它哪天转红，说明两种写法真的分道了，
#: 那时这条记录就得改，而不是把断言改松。
SUBDOMAIN_RULE = (
    '    if (host == "douyin.com" or host.endswith(".douyin.com")\n'
    '            or host == "iesdouyin.com" or host.endswith(".iesdouyin.com")):'
)

#: 反向对照：改一句与行为无关的注释。转红 = 用例对注释有意见，那是守卫越权。
NOTE_FROM = "#: 抖音同理要锚定：`/video/123/a` 不是一条视频。"
NOTE_TO = "#: 抖音同理要锚定：`/video/123/a` 不是一条视频（改这句不该有任何人红）。"

MUTATIONS = [
    {
        "id": "C1", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "名单缩成 www.douyin.com 一个：iesdouyin 那种形态失去依据",
        "find": HOSTS, "replace": '_DOUYIN_HOSTS = {"www.douyin.com"}',
    },
    {
        "id": "C2", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "去掉裸域 douyin.com：无 www 的形态失去依据",
        "find": HOSTS,
        "replace": ('_DOUYIN_HOSTS = {"www.douyin.com", "iesdouyin.com", '
                    '"www.iesdouyin.com"}'),
    },
    {
        "id": "C3", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "去掉裸域 iesdouyin.com：分享那种形态失去依据",
        "find": HOSTS,
        "replace": ('_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", '
                    '"www.iesdouyin.com"}'),
    },
    {
        "id": "C4", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "判定恒真：别人家的 host 只要 path 像抖音视频就被改写",
        "find": BRANCH, "replace": "    if True:",
    },
    {
        "id": "C7", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "只删 www.iesdouyin.com（工单点名的那一条）：分享形态失去依据",
        "find": HOSTS,
        "replace": ('_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", '
                    '"iesdouyin.com"}'),
    },
    {
        "id": "C5", "expect": "KILLED", "file": CANON, "hits": 1,
        "why": "判定恒假：抖音那几种形态全部不再归一",
        "find": BRANCH, "replace": "    if False:",
    },
    {
        "id": "C6", "expect": "GREEN", "file": CANON, "hits": 1,
        "why": "等价改写：精确匹配换成子域判定，用户真会粘的形态结果不变",
        "find": BRANCH, "replace": SUBDOMAIN_RULE,
    },
    {
        "id": "R1", "expect": "GREEN", "file": CANON, "hits": 1,
        "why": "反向对照：改一句注释",
        "find": NOTE_FROM, "replace": NOTE_TO,
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
                  "——先问「是文件的问题还是我锚点的问题」")
            bad += 1
            continue
        write(m["file"], src.replace(m["find"], m["replace"], 1))
        try:
            code, out, ran = run_tests()
        finally:
            # 还原命令自己也会坏：run_tests 抛异常时不能把生产源码留在变异态。
            write(m["file"], src)

        if ran == 0:
            verdict = "NO-TESTS-RAN"
        elif code != 0:
            verdict = "KILLED"
        else:
            verdict = "GREEN"
        has_assertion = "AssertionError" in out
        ok = verdict == m["expect"] and (verdict != "KILLED" or has_assertion)
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {verdict} (期望 {m['expect']})"
              f"  ran={ran}  AssertionError={'有' if has_assertion else '无'}"
              f"  exit={code}  — {m['why']}")
        if not ok:
            print(out[-700:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)