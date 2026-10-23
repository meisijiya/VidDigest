"""工单 #54 的变异 harness。

四条变异必须 KILLED，一条反向对照必须保持绿。

M1 与 M2 是**回归守卫**：把刚修掉的 `StripeObject.get()` 缺陷改回去，
新测试必须转红。没有它们，这个修复下一次重构就会被无声撤销。

M3 守的是幂等本身：`database.complete_order` 里那句 `status = 'pending'`。
"""
import io
import subprocess
import sys

from venv_python import venv_python

PY = venv_python()
TARGET = "backend/tests/test_payment_webhook.py"
PAYMENT = "backend/api_payment.py"
DATABASE = "backend/database.py"


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


NLP, NLD = eol_of(PAYMENT), eol_of(DATABASE)


def n(*lines, nl=NLP):
    return nl.join(lines)


MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "file": PAYMENT, "hits": 1,
        # 恢复缺陷 = 恢复崩溃，所以它必然以 AttributeError 而不是断言失败转红。
        # 这是**预期形态**：guard 要检出的正是那次崩溃。
        # 不放宽判据，而是把两种 KILLED 分开记，报告里写明是哪一种。
        "crash_marker": "AttributeError: 'get' is a dict method",
        "why": "把 StripeObject 缺陷改回去：session.get('payment_status')",
        "find": n('        if _stripe_field(session, "payment_status") == "paid":'),
        "replace": n('        if session.get("payment_status") == "paid":'),
    },
    {
        "id": "M2", "expect": "KILLED", "file": PAYMENT, "hits": 1,
        "crash_marker": "AttributeError: 'get' is a dict method",
        "why": "异步分支同样改回 session.get('payment_intent')",
        "find": n('        payment_intent_id = _stripe_field(session, "payment_intent")',
                  "        complete_order(session[\"id\"], payment_intent_id)"),
        "replace": n('        payment_intent_id = session.get("payment_intent", "")',
                     "        complete_order(session[\"id\"], payment_intent_id)"),
    },
    {
        "id": "M3", "expect": "KILLED", "file": DATABASE, "hits": 1,
        "why": "去掉 complete_order 的 status='pending' 过滤：幂等失效",
        "find": n('            "SELECT * FROM orders WHERE stripe_session_id = ? AND status = \'pending\'",',
                  nl=NLD),
        "replace": n('            "SELECT * FROM orders WHERE stripe_session_id = ?",', nl=NLD),
    },
    {
        "id": "M4", "expect": "KILLED", "file": PAYMENT, "hits": 1,
        "why": "两种 400 不再可区分：签名失败也回 not configured",
        "find": n('content={"error": "Invalid signature"}'),
        "replace": n('content={"error": "Webhook secret not configured"}'),
    },
    {
        "id": "R1", "expect": "GREEN", "file": PAYMENT, "hits": 1,
        "why": "反向对照：订单号前缀与本文件断言无关",
        "find": n('    return f"SA{ts}{user_id:04d}{short_uuid}"'),
        "replace": n('    return f"XX{ts}{user_id:04d}{short_uuid}"'),
    },
]

bad = 0
print(f"行尾：api_payment={NLP!r}  database={NLD!r}")
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
        marker = m.get("crash_marker")
        crashed = bool(marker) and marker in out
        # 三种结局都算「符合预期」，但必须在输出里写明是哪一种：
        # KILLED(assert) 断言失败 / KILLED(crash) 恢复了那次崩溃 / GREEN 反向对照。
        if verdict == "GREEN":
            ok = m["expect"] == "GREEN"
            how = "GREEN"
        elif has_assertion:
            ok = m["expect"] == "KILLED"
            how = "KILLED(assert)"
        elif crashed:
            ok = m["expect"] == "KILLED"
            how = "KILLED(crash)"
        else:
            ok = False
            how = "KILLED(其它)"
        if not ok:
            bad += 1
        print(f"[{'OK ' if ok else 'BAD'}] {m['id']} {how} (期望 {m['expect']})"
              f"  exit={code}  — {m['why']}")
        if not ok:
            print(out[-800:])

print(f"\n收尾自查：{bad} 条异常")
sys.exit(1 if bad else 0)