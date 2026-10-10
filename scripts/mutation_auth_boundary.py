"""工单 #51 的变异 harness：证明新测试真有判别力。

四条变异 M1-M4 必须 KILLED，一条反向对照 R1 必须保持绿。

三条纪律（来自本仓已被复审打过的坑）：
  · **跑前预检**：逐条核对 anchor 在源文件里的命中数，要求恰好 1。
    「0 命中」有两条成因——锚点写错 / 文件里没这段代码——输出完全一样，
    而诊断方向天然指向后者。不预检就会把「锚点写错」误判成「断言没判别力」。
  · **KILLED 必须伴随 AssertionError**：只看退出码会把「变异把文件改成语法错误」
    当成「断言咬住了」。所以这里显式抓输出里的 AssertionError。
  · **收尾必看 git diff**：还原命令本身也会坏，源文件留在变异态里的症状是
    「测试在变异态下本来就该红」，任何测试都不会告诉你这件事。

用 `open(..., newline='')` 读写，不碰行尾；锚点用 `NL.join([...])` 拼，
不用三引号、不用 '\\n' 转义——这两种写法的 repr 看起来一样，肉眼核对看不出来。
"""
import io
import subprocess
import sys

PY = "backend/venv/Scripts/python.exe"
TARGET = "backend/tests/test_auth_validation.py"
AUTH = "backend/auth.py"
API_AUTH = "backend/api_auth.py"

NL = "\r\n" if open(AUTH, "rb").read().count(b"\r\n") else "\n"


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


MUTATIONS = [
    {
        "id": "M1", "file": AUTH, "expect": "KILLED",
        "why": "边界偏一位：> 50 改成 >= 50，恰好 50 位的用户注册不了",
        "lines": ["    if len(password) > 50:"],
        "lines_to": ["    if len(password) >= 50:"],
    },
    {
        "id": "M2", "file": AUTH, "expect": "KILLED",
        "why": "去掉随机盐：同一密码两次哈希相同",
        "lines": ['    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()'],
        "lines_to": ['    return bcrypt.hashpw(password.encode(), b"$2b$12$ggmFPc3RybbDm1HQ6PM41.").decode()'],
    },
    {
        "id": "M3", "file": API_AUTH, "expect": "KILLED",
        "why": "登录失败泄露是哪个字段错了",
        "lines": ["    user = get_user_by_email(req.email)",
                  "    if not user:",
                  '        raise HTTPException(status_code=400, detail="邮箱或密码错误")'],
        "lines_to": ["    user = get_user_by_email(req.email)",
                     "    if not user:",
                     '        raise HTTPException(status_code=400, detail="邮箱不存在")'],
    },
    {
        "id": "M4", "file": API_AUTH, "expect": "KILLED",
        "why": "把密码校验整段摘掉",
        "lines": ["    err = validate_password(req.password)",
                  "    if err:",
                  "        raise HTTPException(status_code=400, detail=err)"],
        "lines_to": ["    err = None"],
    },
    {
        "id": "R1", "file": AUTH, "expect": "GREEN",
        "why": "反向对照：JWT 过期时长与本文件断言无关，必须保持绿",
        "lines": ["JWT_EXPIRE_HOURS = 72"],
        "lines_to": ["JWT_EXPIRE_HOURS = 999"],
    },
]

bad = 0
for m in MUTATIONS:
    # 自检：缺 lines_to 的变异定义，其 anchor 会被拼成「原行 + 变异行」两行，
    # 永远 0 命中——输出形状与「锚点写错」完全一样。不显式挡住它，
    # 就会把 harness 自己的错误判成「断言没判别力」。
    if "lines_to" not in m:
        print(f"[HARNESS ERROR] {m['id']} 缺 lines_to——先修 harness，不要动断言")
        bad += 1
        continue
    src = read(m["file"])
    anchor = NL.join(m["lines"])
    hits = src.count(anchor)
    if hits != 1:
        print(f"[PRECHECK BAD] {m['id']} anchor 命中 {hits} 次（{m['file']}）"
              f"——先问「是文件的问题还是我锚点的问题」")
        bad += 1
        continue
    mutated = src.replace(anchor, NL.join(m["lines_to"]), 1)
    write(m["file"], mutated)
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
    if not has_assertion and verdict == "KILLED":
        print(out[-900:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)