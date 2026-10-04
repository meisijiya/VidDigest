#!/usr/bin/env python
"""内容级敏感信息扫描。init.sh 的第 4 道关卡。

退出 0 = 干净；退出 1 = 检出问题。

**为什么这道关卡存在**：`.gitignore` 是**文件名级**的，它挡得住 `.env`，
挡不住「把 `.env` 里那条口令抄进 docs/OPERATIONS.md」。本仓库真的发生过一次：
`TEST_ACCOUNTS.md` 按规矩移出了版本库，可同一批明文口令与本机用户名留在了
运维手册和一个 agent 日志目录里，直到推上 PUBLIC 远端。护栏必须在**内容**上。

**为什么扫两个范围**：只扫 HEAD 的关卡给出的是「当前这棵树干净」，
不是「历史干净」。本轮就是这么漏的：另一个 Windows 用户名（`backend/summarizer.py`
的两处 ffmpeg 兜底路径）只存在于历史 blob 里，还有一条 commit message 把那个
路径原样写进了提交说明 —— 两者 HEAD 扫描都看不见。所以这里同时扫：

  范围 1  工作树里 git 跟踪的文件
  范围 2  全部**可达**历史（blob 内容 + commit message）

只扫可达对象：不可达的旧对象既不在远端也不在任何 clone 上。
两个范围的结论分开报，不合并成一个「通过」，免得看起来像单一断言。

**自引用**：本文件自己也在扫描范围内。正则里如果出现字面量形态的待查串
（形如「盘符冒号 + 反斜杠 + Users + 反斜杠」的那种），关卡会把自己判红。
所以所有含反斜杠的模式都用 `_B` 拼出来，源码里不出现相邻字面量 ——
改注入串的写法，不改判据。这条不是设想的：本文档字符串第一版就写了那个
形态的示例，关卡当场把自己报红。
"""

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO, "backend", ".env")

# 反斜杠：正则里要两个字节才匹配一个字面反斜杠。拼出来，避免源码自匹配。
_B = "\\\\"
_SEP = "[/\\\\]+"

# ── A：什么算「真值」 ─────────────────────────────────────────
SECRET_KEY_RE = re.compile(r"(?i)(SECRET|API_?KEY|_KEY$|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE)")
PLACEHOLDER_RE = re.compile(
    r"(?i)(change[-_ ]?in[-_ ]?production|^your[-_]|^xxx|^<|placeholder|example"
    r"|^sk-(your|test|xxx)|^price_|^whsec_)"
)

# ── B / C：形态 ───────────────────────────────────────────────
# 括号里的字节串会同时用于字节正则（扫历史 blob）与字符串正则（扫工作树文本）。
SHAPE_PATTERNS = [
    ("openai-style-key", rb"sk-[A-Za-z0-9]{20,}"),
    ("anthropic-style-key", rb"sk-ant-[A-Za-z0-9\-_]{20,}"),
    ("github-token", rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("aws-access-key-id", rb"AKIA[0-9A-Z]{16}"),
    ("google-api-key", rb"AIza[0-9A-Za-z\-_]{30,}"),
    ("stripe-live-key", rb"sk_live_[0-9a-zA-Z]{10,}"),
    ("slack-token", rb"xox[abprs]-[0-9A-Za-z\-]{10,}"),
    ("json-web-token", rb"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),
    ("private-key-block",
     rb"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
]

PATH_PATTERNS = [
    # 本机家目录：用户名是 PII，也是部署拓扑的意外泄露。
    # 捕获组 2 是用户名 —— 豁免判定必须对**捕获到的标识符做精确比对**。
    # 用子串包含判定过一次，漏了：`'user' in b'sample-user'` 为真，
    # 于是 notuser / abuser 这类名字会被一起放过。变异用例 M8 守着这条。
    ("windows-user-home",
     ("([A-Za-z]:" + _SEP + "Users" + _SEP + ")([A-Za-z0-9_.\\-]+)").encode()),
    ("posix-home", rb"/home/([A-Za-z0-9_.\-]+)/"),
    # 具体安装根 / 备份落点。文档要写运维手册就该用 <app> 占位。
    ("prod-install-path", rb"/(?:opt|srv|backup)/([A-Za-z0-9_.\-]+)"),
]
# 上面这些模式里，「要豁免的那个标识符」是第几组。前两个是 2（前面还有盘符组），
# posix-home / prod-install-path 是 1。
IDENT_GROUP = {"windows-user-home": 2, "posix-home": 1, "prod-install-path": 1}

# 口令字面量：只查文档/配置，源码里的密码变量与测试夹具不查。
# 全字节拼装：str 与 bytes 不能相加，中文词要单独 encode 后接上去。
_PASSWORD_WORD = (b"(?:password|passwd|pwd|"
                  + "密码".encode("utf-8") + b"|"
                  + "口令".encode("utf-8") + b")")
PASSWORD_LITERAL_RE = _PASSWORD_WORD + rb"\s*[=:]\s*[\"'][^\"'\n]{8,}[\"']"

# 表格密码列：事故的原始形态是 markdown 表格里的一整格字面量，
# 靠「表头写着密码 + 该列是反引号字面量」定位，不靠字面量形态。
TABLE_PASSWORD_WORD_RE = re.compile(
    (rb"(?i)password|passwd|pwd|") + "密码".encode("utf-8") + rb"|" + "口令".encode("utf-8"))

# 放行名单。**只对测试文件生效** —— 早先它对所有文件生效，于是「文档里写了
# 那一行测试账号的密码」也被放行，而那正是本仓库事故的形态。
# 域名可以出现在文档里，密码不行。
ALLOW_SUBSTRINGS = (b"@example.com", b"@example.org", b"@x.com", b"@x.test", b"@api.example.com")

# 豁免是「显式登记的合法类型」，不是把判据放松。每一项都要说清为什么合法。
# 名单里**不放测试夹具里用的名字** —— 放了就等于为了让自己的夹具过关而放松判据。
# 变异测试当场抓过这个：M2 用的 `sample-user` 一度在这份名单里，于是那条用例
# 再也不转红，而它测的正是「本机家目录路径」这条规则。
BENIGN_CAPTURE = {
    "windows-user-home": {"<user>", "user", "username", "YOUR_USER", "YourName"},
    "posix-home": {"user", "runner", "ubuntu"},
    "prod-install-path": set(),      # 占位符 <app> 不匹配该字符类，天然不命中
}

# 已经清掉的历史里，密码格剩下的是脱敏标记而不是凭据。
REDACTION_MARKERS = (b"\xe5\xb7\xb2\xe7\xa7\xbb\xe9\x99\xa4",  # 已移除
                     b"REDACTED", b"[removed]", b"<removed>")

TEST_PATH_MARKERS = ("/tests/", "test_", ".test.", "/test/")
DOC_SUFFIXES = (".md", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
                ".sh", ".bat", ".ps1", ".env", ".example", ".txt")
MAX_FILE_BYTES = 4_000_000
BATCH = 400


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True)


def is_test_file(path):
    low = "/" + path.replace("\\", "/").lower()
    return any(m in low for m in TEST_PATH_MARKERS)


def is_doc_file(path):
    low = path.lower()
    return low.endswith(DOC_SUFFIXES) or "/docs/" in "/" + low


def line_text_at(blob, pos):
    ls = blob.rfind(b"\n", 0, pos) + 1
    le = blob.find(b"\n", pos)
    return blob[ls:le if le != -1 else len(blob)]


def real_secret_values():
    """从 backend/.env 取真值。返回 (值列表, 状态说明)。"""
    if not os.path.isfile(ENV_PATH):
        return [], "backend/.env 不存在（全新克隆），跳过 A 维度"
    values = []
    with open(ENV_PATH, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if len(val) < 8 or PLACEHOLDER_RE.search(val):
                continue
            if not SECRET_KEY_RE.search(key):
                continue
            values.append((key, val))
    if not values:
        return [], "backend/.env 里没有可判定的密钥类真值，跳过 A 维度"
    return values, "已载入 %d 个密钥类真值（%s）" % (
        len(values), ", ".join(k for k, _ in values))


def md_table_password_cells(blob, path):
    """markdown 表格中「密码 / 口令」列里的反引号字面量。

    已知局限（如实写明而不是假装覆盖）：
      1. **没有表头的裸数据行**抓不到 —— 规则靠表头定位「哪一列是密码」。
      2. 表头没写「密码/口令/password」而语义上是密码的列，抓不到。
    对付这两类靠纪律：任何文件（含注释与测试夹具）都不写原值。
    """
    hits = []
    state = "idle"      # idle -> header -> data
    pw_col = None
    for idx, line in enumerate(blob.split(b"\n"), 1):
        s = line.strip()
        if not (s.startswith(b"|") and s.endswith(b"|")):
            state, pw_col = "idle", None
            continue
        cells = [c.strip() for c in s.strip(b"|").split(b"|")]
        is_sep = bool(cells) and all(set(c) <= set(b"-: ") for c in cells if c)
        if state == "idle":
            if is_sep:
                continue
            state, pw_col = "header", None
            for ci, c in enumerate(cells):
                if TABLE_PASSWORD_WORD_RE.search(c):
                    pw_col = ci
            continue
        if state == "header":
            state = "data"          # 下一行是分隔行，跳过
            continue
        if pw_col is not None and pw_col < len(cells):
            m = re.search(rb"`([^`\n]{4,})`", cells[pw_col])
            if m and not any(x in m.group(1) for x in REDACTION_MARKERS):
                hits.append((path, idx))
    return hits


def is_benign_ident(name, match):
    """豁免判定：把捕获到的标识符**精确**与豁免名单比对。

    不用子串包含。踩过：`'user' in b'sample-user'` 为真，于是 notuser /
    abuser 这类含豁免词的用户名会被一起放过 —— 而那是一条真实用户名该被
    抓住的形态。豁免是「显式登记的合法标识符」，不是「含有某个词」。
    """
    gi = IDENT_GROUP.get(name)
    if gi is None or gi > (match.lastindex or 0):
        return False
    ident = match.group(gi)
    return ident.decode("utf-8", "replace") in BENIGN_CAPTURE.get(name, ())


def scan_bytes(blob, path, secrets, allow_fixtures):
    """对一段字节跑全部维度，返回 (维度, 行号, 规则) 列表。"""
    out = []

    for key, val in secrets:
        needle = val.encode("utf-8", "replace")
        start = 0
        while True:
            p = blob.find(needle, start)
            if p == -1:
                break
            out.append(("A-真凭据值", blob[:p].count(b"\n") + 1, key))
            start = p + len(needle)

    for name, pat in SHAPE_PATTERNS:
        for m in re.finditer(pat, blob):
            if allow_fixtures and any(a in line_text_at(blob, m.start())
                                      for a in ALLOW_SUBSTRINGS):
                continue
            out.append((name, blob[:m.start()].count(b"\n") + 1, "形态"))

    for name, pat in PATH_PATTERNS:
        for m in re.finditer(pat, blob):
            if is_benign_ident(name, m):
                continue
            if allow_fixtures and any(a in line_text_at(blob, m.start())
                                      for a in ALLOW_SUBSTRINGS):
                continue
            out.append((name, blob[:m.start()].count(b"\n") + 1, "本机路径/部署拓扑"))

    if is_doc_file(path):
        for m in re.finditer(PASSWORD_LITERAL_RE, blob):
            out.append(("B-inline-password-literal",
                        blob[:m.start()].count(b"\n") + 1, "文档口令字面量"))
        for _p, ln in md_table_password_cells(blob, path):
            out.append(("B-md-table-password-cell", ln, "表格密码列字面量"))
    return out


def reachable_blobs():
    d = {}
    for line in git("rev-list", "--objects", "--all").stdout.decode("utf-8", "replace").splitlines():
        p = line.split(" ", 1)
        if len(p) == 2:
            d.setdefault(p[0], p[1])
    return d


def batch_read(shas):
    proc = subprocess.Popen(["git", "-C", REPO, "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out, _ = proc.communicate(("\n".join(shas) + "\n").encode())
    i = 0
    while i < len(out):
        nl = out.find(b"\n", i)
        if nl == -1:
            break
        h = out[i:nl].decode("utf-8", "replace").split()
        i = nl + 1
        if len(h) < 3:
            break
        n = int(h[2])
        yield h[0], out[i:i + n]
        i += n + 1


def main():
    secrets, status = real_secret_values()
    head_fail, hist_fail = [], []

    # ── 范围 1：工作树里 git 跟踪的文件 ────────────────────────
    files = git("ls-files").stdout.decode("utf-8", "replace").splitlines()
    for rel in files:
        full = os.path.join(REPO, rel)
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_FILE_BYTES:
            continue
        try:
            with open(full, "rb") as fh:
                blob = fh.read()
        except OSError:
            continue
        for dim, ln, rule in scan_bytes(blob, rel, secrets, is_test_file(rel)):
            head_fail.append((rel, ln, dim, rule))

    # ── 范围 2：全部可达历史（blob + commit message）──────────
    m = reachable_blobs()
    keys = list(m)
    for i in range(0, len(keys), BATCH):
        for sha, blob in batch_read(keys[i:i + BATCH]):
            if b"missing" in blob[:20] or len(blob) > MAX_FILE_BYTES:
                continue
            path = m.get(sha, "?")
            for dim, ln, rule in scan_bytes(blob, path, secrets, is_test_file(path)):
                hist_fail.append((sha[:7], ln, dim, rule))

    for line in git("log", "--all", "--format=%B").stdout.split(b"\n"):
        if not line.strip():
            continue
        for name, pat in SHAPE_PATTERNS:
            if re.search(pat, line):
                hist_fail.append(("<commit-message>", 0, name, "提交说明"))
        for name, pat in PATH_PATTERNS:
            mth = re.search(pat, line)
            if mth and not is_benign_ident(name, mth):
                hist_fail.append(("<commit-message>", 0, name, "提交说明"))

    # ── 结论行由失败标志推导 ─────────────────────────────────
    print("范围 1 工作树：%d 个跟踪文件" % len(files))
    print("  " + status)
    print("范围 2 可达历史：%d 个对象（blob 内容 + commit message）" % len(keys))
    print("")
    for label, rows in (("工作树", head_fail), ("历史", hist_fail)):
        if rows:
            print("[FAIL] %s 检出 %d 处：" % (label, len(rows)))
            for ref, ln, dim, rule in rows[:40]:
                print("   %-9s L%-6s %-22s %s" % (ref, ln, dim, rule))
            if len(rows) > 40:
                print("   ... 还有 %d 处" % (len(rows) - 40))
        else:
            print("[PASS] %s：未检出敏感内容" % label)
    print("")
    if head_fail or hist_fail:
        print("处置：值已经落到远端就不是「待定」——先轮换，再清历史，最后才谈补 .gitignore。")
        return 1
    print("PASS：工作树与可达历史均未检出敏感内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
