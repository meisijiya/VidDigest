"""变异脚本：证明「邀请码注册」那批测试真能杀掉缺陷（工单 #99）。

逐个破坏 → 跑指定测试 → 必须转红，而且必须是**断言**转红。绿灯不算证据。

## 为什么值得单独守

「注册要邀请码」是一串**看不见**的约定：码得校验、消费得原子、失效原因得
分开说、列表不得回显完整码。这些约定写错了，功能测试可能照样全绿——账号
照建、照发 token，只是闸门形同虚设。

所以只能靠「故意打坏 + 看它有没有变红」来证明守得住：测试写完不代表
它在守，只代表它在**绿**。

## 判据：KILLED 必须伴随 AssertionError

语法错误、导入失败、模块炸掉——这些会让测试「红」，但它们证明的是
「我改坏了代码」，不是「这条用例在守」。所以脚本额外看输出里有没有
`AssertionError`：没有就当这条用例没守住。

## 每条变异钉住一个具体后果

| 变异 | 现实里的后果 |
| --- | --- |
| M1 | 注册不校验邀请码也能建号，闸门整个敞开 |
| M2 | 码被「消费」了但计数不涨，同一个名额能被用无数次 |
| M3 | 已作废、已过期的码照样能注册 |
| M4 | 管理端列表回显完整码，码从只给一次变成人人都能翻出来 |
| M5 | 作废端点回 200 但没真作废，管理员以为撤了其实没撤 |
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from venv_python import venv_python  # noqa: E402

# Windows 控制台默认不是 UTF-8，中文说明会变成乱码——这份输出是证据，
# 证据得能读。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TESTS = Path(__file__).resolve().parent
BACKEND = TESTS.parent
REPO = BACKEND.parent
PY = venv_python()
AUTH_FILE = REPO / "backend" / "api_auth.py"
DATABASE_FILE = REPO / "backend" / "database.py"
TARGET = ["tests/test_invite_codes.py"]

#: (说明, 目标文件, [(模式串, 替换串), ...])
#:
#: 一条变异可以带多处替换——但必须都落在**同一个文件**里，且是同一个
#: 缺陷的不同侧面（M1 就是：不校验码 → 直接建号）。
MUTATIONS = [
    (
        "M1 注册不校验邀请码就直接建号：闸门整个敞开",
        AUTH_FILE,
        [
            (
                "from database import InviteRegistrationError, get_user_by_email,"
                " register_with_invite_code\n",
                "from database import InviteRegistrationError, create_user,"
                " get_user_by_email, register_with_invite_code\n",
            ),
            (
                "    try:\n"
                "        user = register_with_invite_code(req.email, hashed, code)\n"
                "    except InviteRegistrationError as exc:\n"
                "        raise HTTPException(status_code=400, detail=exc.detail) from None\n",
                "    user = create_user(req.email, hashed)\n",
            ),
        ],
    ),
    (
        "M2 消费了码但 used_count 不递增：同一个名额能被用无数次",
        DATABASE_FILE,
        [
            (
                '            "UPDATE invite_codes SET used_count = used_count + 1"\n',
                '            "UPDATE invite_codes SET used_count = used_count"\n',
            ),
        ],
    ),
    (
        "M3 不判作废与过期：废码照样能注册",
        DATABASE_FILE,
        [
            (
                '        if row["revoked"]:\n'
                '            raise InviteRegistrationError("邀请码已作废")\n'
                '        if _invite_code_expired(row["expires_at"]):\n'
                '            raise InviteRegistrationError("邀请码已过期")\n',
                "        pass  # 变异：不再判作废与过期\n",
            ),
        ],
    ),
    (
        "M4 列表回显完整码：码从「只给一次」变成人人都能翻出来",
        DATABASE_FILE,
        [
            (
                '    if include_code:\n'
                '        item["code"] = row["code"]\n'
                '    else:\n'
                '        item["code_masked"] = mask_invite_code(row["code"])\n',
                '    if include_code:\n'
                '        item["code"] = row["code"]\n'
                '    else:\n'
                '        item["code"] = row["code"]\n'
                '        item["code_masked"] = mask_invite_code(row["code"])\n',
            ),
        ],
    ),
    (
        "M5 作废没写进库：管理员以为撤了，其实码还能用",
        DATABASE_FILE,
        [
            (
                '            "UPDATE invite_codes SET revoked = 1 WHERE id = ?", (invite_id,)\n',
                '            "UPDATE invite_codes SET revoked = 0 WHERE id = ?", (invite_id,)\n',
            ),
        ],
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
#: **判据在守**。收集期的错误（语法错、导入错、模块炸掉）不算——那说明
#: 的是「我改坏了代码」。
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
    print(f"基线 {len(TARGET)} 个测试文件: {summary}\n")

    originals = {
        AUTH_FILE: read_source(AUTH_FILE),
        DATABASE_FILE: read_source(DATABASE_FILE),
    }
    survived, not_run = [], []

    try:
        for name, target_file, edits in MUTATIONS:
            original = originals[target_file]
            mutated = original
            for pattern, replacement in edits:
                mutated = mutate_bytes(mutated, pattern, replacement)
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
    print(
        "\n还原自检: api_auth.py / database.py 逐字节一致"
    )

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 邀请码注册的测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，邀请码注册守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
