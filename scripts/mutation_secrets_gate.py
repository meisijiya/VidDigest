"""变异确认：关卡必须真的有牙。

按纪律，「跑完是绿的」不能证明关卡有效——必须把每一种要抓的形态故意放回去，
确认它转红，再确认原样恢复。只验当前状态 = 没验。

每个用例都断言两件事：
  1. 注入后关卡退出码为 1（不是 review，不是 0）
  2. 恢复后文件逐字节等于注入前，且关卡回到 0

第 2 条同样重要：注入动作本身可能把文件写坏，而「恢复失败」在只查退出码时看不见。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "backend", "venv", "Scripts", "python.exe")
SCANNER = os.path.join(REPO, "scripts", "check_secrets.py")
DOC = os.path.join(REPO, "docs", "OPERATIONS.md")


def run_scanner():
    p = subprocess.run([PY, SCANNER], capture_output=True, cwd=REPO)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def real_env_value(key):
    """取 backend/.env 里的真值，只在本进程内使用，不打印。"""
    with open(os.path.join(REPO, "backend", ".env"), encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            if raw.strip().startswith(key + "="):
                return raw.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return None


CASES = [
    # (用例名, 注入文本, 期望—— 必须 FAIL)
    # M1 逐字复现事故原形：表头含「密码」列 + 分隔行 + 该列的明文口令。
    # 早先这里只注入数据行、没有表头，于是「表头定位」这条规则正确地没报警 ——
    # 那是样本畸形，不是关卡弱。变异必须复现真实产物的形状，否则测的是别的东西。
    ("M1 明文口令落进运维手册（事故原形，含表头）",
     "\n| 邮箱 | 密码 | VIP | AI 配额 | 用途 |\n"
     "|---|---|:---:|:---:|---|\n"
     "| `demo@example.org` | `Demo@2026` | ❌ | 3 次/日 | 演示 |\n", True),
    # M2 复现真实产物的**形状**：一条带盘符、带用户名的 Windows 家目录绝对路径。
    # 注释里不写原值，注入串里也不用真名 —— 这个文件是要入库的，
    # 把真实用户名抄进来等于把它从历史里清掉之后又写回一个新文件。
    # 守卫 tests/test_ffmpeg_discovery.py 正是在盯这件事，它先于我发现了这处。
    # （第一版我在注释里贴了原文，门禁立刻红了：那条守卫不是噪声，它是对的。）
    ("M2 本机用户名绝对路径（与真实产物同形）",
     "\n工作区：`" + "C" + ":" + chr(92) + "Users" + chr(92) +
     "sample-user" + chr(92) + "workdir`\n", True),
    # M3 具体安装根目录（systemd 的 WorkingDirectory 形态）。
    # 同样要拆开写：这个文件是要入库的，而它正好是关卡的测试夹具。
    # 如果注入串以字面量相邻的形式出现在本文件源码里，关卡会把**自己**判红 ——
    # 第一次跑就是这样。给关卡加「跳过测试夹具」的豁免是更差的选择：那等于
    # 为放松判据找理由。所以改注入串的写法，不改判据。
    ("M3 systemd 具体部署拓扑",
     "\nWorkingDirectory=/" + "opt" + "/" + "secret-prod-dir/backend\n", True),
    ("M4 合成夹具不应误报（@example.com）",
     "\n| `a@example.com` | `password: fixture-pass-1234` |\n", False),
    ("M5 短示例口令（6 位）不应误报",
     '\n请求示例：{"email": "u@example.com", "password": "123456"}\n', False),
    # M7 表头里没有「密码」二字时，同形态的表不该报警 ——
    # 这条守住 M1 的规则不会退化成「见到反引号就报」。
    ("M7 非密码列表格不应误报（函数名表）",
     "\n| 函数 | 行 |\n|---|---|\n| `hash_password` | 17 |\n"
     "| `verify_password` | 21 |\n", False),
    # M8 豁免判定必须是**精确**比对，不能是子串包含。
    # 踩过：豁免名单里有 `user`，而实现写成子串包含，于是 `sample-user`、
    # `notuser`、`abuser` 全被放过 —— 那是一条真实用户名该被抓住的形态。
    ("M8 用户名含豁免词仍须被抓（精确比对）",
     "\n工作区：`" + "C" + ":" + chr(92) + "Users" + chr(92) +
     "not" + "user" + chr(92) + "w`\n", True),
    # M9 反向：恰好等于豁免名的标识符必须放行，否则豁免名单形同虚设、
    # 真被替换成 <user> 的历史会一直报红，门禁会被当成噪声忽略。
    ("M9 恰为豁免名的标识符应放行",
     "\n工作区：`" + "C" + ":" + chr(92) + "Users" + chr(92) +
     "user" + chr(92) + "w`\n", False),
]

print("=" * 70)
print("变异确认：scripts/check_secrets.py 是否有牙")
print("=" * 70)

fails = []

rc, out = run_scanner()
print("\n[基线] 退出码=%d  期望=0  -> %s" % (rc, "OK" if rc == 0 else "BAD"))
if rc != 0:
    fails.append("基线就已经是红的")

for name, injection, expect_fail in CASES:
    with open(DOC, "rb") as fh:
        before = fh.read()
    try:
        with open(DOC, "ab") as fh:   # 追加，字节级不改原内容
            fh.write(injection.encode("utf-8"))
        rc, out = run_scanner()
        verdict = "OK" if ((rc == 1) == expect_fail) else "BAD"
        if verdict == "BAD":
            fails.append("%s（期望 fail=%s，实得 rc=%d）" % (name, expect_fail, rc))
        print("\n[%s] %s" % (verdict, name))
        print("     期望 fail=%s，实得 rc=%d" % (expect_fail, rc))
        # 打印关卡指认的行，便于核对它抓的是不是注入那一行
        for ln in out.splitlines():
            if "OPERATIONS.md" in ln and ("literal" in ln or "C-" in ln):
                print("     关卡指认: %s" % ln.strip())
    finally:
        with open(DOC, "wb") as fh:
            fh.write(before)
        with open(DOC, "rb") as fh:
            restored = fh.read()
        same = restored == before
        if not same:
            fails.append("%s：恢复后文件与注入前不一致（注入动作写坏了文件）" % name)
        print("     恢复: %s" % ("逐字节一致" if same else "!!! 不一致 !!!"))

# A 维度单独验：真值反查必须能转红
key = "ALIYUN_BAILIAN_API_KEY"
val = real_env_value(key)
if val and len(val) >= 8:
    target = os.path.join(REPO, "docs", "ADR_TMP.md")
    try:
        with open(target, "wb") as fh:
            fh.write(("# tmp\nALIYUN_BAILIAN_API_KEY=%s\n" % val).encode("utf-8"))
        # 新文件未跟踪，扫描器只看 git ls-files，因此先 stage 它
        subprocess.run(["git", "-C", REPO, "add", "--", "docs/ADR_TMP.md"],
                       capture_output=True)
        rc, out = run_scanner()
        verdict = "OK" if rc == 1 else "BAD"
        if rc != 1:
            fails.append("M6 A 维度真值反查没转红（rc=%d）——最关键的一条" % rc)
        print("\n[%s] M6 A 维度：把 .env 真值放进一个已 stage 的文档" % verdict)
        print("     期望 fail=True，实得 rc=%d" % rc)
        for ln in out.splitlines():
            if "ADR_TMP" in ln:
                print("     关卡指认: %s" % ln.strip())
    finally:
        subprocess.run(["git", "-C", REPO, "rm", "--cached", "-q", "--",
                        "docs/ADR_TMP.md"], capture_output=True)
        if os.path.exists(target):
            os.remove(target)

rc, _ = run_scanner()
print("\n[收尾] 退出码=%d  期望=0  -> %s" % (rc, "OK" if rc == 0 else "BAD"))
if rc != 0:
    fails.append("收尾不是绿的，说明有残留注入")

print("")
print("=" * 70)
if fails:
    print("RESULT: 关卡不可信，共 %d 项不合格" % len(fails))
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("RESULT: 每个注入形态都按预期转红，恢复后逐字节一致，基线与收尾均为绿")
sys.exit(0)
