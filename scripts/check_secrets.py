#!/usr/bin/env python
"""内容级敏感信息扫描。init.sh 的第 4 道关卡。

退出 0 = 干净；退出 1 = 检出问题。

**为什么这道关卡存在**：`.gitignore` 是**文件名级**的，它挡得住 `.env`，
挡不住「把 `.env` 里那条口令抄进 `docs/OPERATIONS.md`」。本仓库真的发生过一次：
`TEST_ACCOUNTS.md` 按规矩移出了版本库，可同一批明文口令与本机用户名
留在了 `docs/OPERATIONS.md` 和一个 agent 日志目录里，直到推上 PUBLIC 远端。
所以护栏必须在**内容**上，不在文件名上。

只扫 git 跟踪的文件：未跟踪的文件不会进版本库，不是这道关卡要管的事。
永远不打印命中的值——只报文件名、行号与规则名。

三个维度：
  A 真实凭据值   —— 从 backend/.env 取真值逐条反查（.env 不存在则跳过并明说）
  B 通用密钥形态 —— 不依赖本机 .env 的固定正则
  C 本机路径     —— 绝对路径带用户名，是 PII，也是部署拓扑的意外泄露
"""

import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(REPO, "backend", ".env")

# ── A：什么算「真值」 ─────────────────────────────────────────
SECRET_KEY_RE = re.compile(r"(?i)(SECRET|API_?KEY|_KEY$|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE)")
# 模板与占位符不是凭据。缺了这份名单，下面每条都会被 .env.example 自己触发。
PLACEHOLDER_RE = re.compile(
    r"(?i)(change[-_ ]?in[-_ ]?production|^your[-_]|^xxx|^<|placeholder|example"
    r"|^sk-(your|test|xxx)|^price_|^whsec_)"
)

# ── B：通用密钥形态 ───────────────────────────────────────────
SHAPE_PATTERNS = [
    ("openai-style-key", rb"sk-[A-Za-z0-9]{20,}"),
    ("anthropic-style-key", rb"sk-ant-[A-Za-z0-9\-_]{20,}"),
    ("github-token", rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("aws-access-key-id", rb"AKIA[0-9A-Z]{16}"),
    ("google-api-key", rb"AIza[0-9A-Za-z\-_]{30,}"),
    ("stripe-live-key", rb"sk_live_[0-9a-zA-Z]{10,}"),
    ("slack-token", rb"xox[abprs]-[0-9A-Za-z\-]{10,}"),
    ("json-web-token", rb"eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}"),
    ("private-key-block", rb"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
]

# 口令回显：只对**文档与配置类文件**生效，且要求右侧是带引号的字面量。
#
# 这条规则是本仓库被咬过一次的地方（明文口令被抄进 docs/OPERATIONS.md），
# 但第一版写得太宽：`password\s*[:=]\s*\S{8,}` 会把
# `const password = createDraft.password` 这类普通变量赋值也报出来。
# 误报会让关卡变成噪声，而噪声关卡的结局是被忽略——那等于没有关卡。
# 收窄成「只查文档/配置 + 只查带引号的字面量」后，两类误报都消失，
# 而真正要抓的那一类仍然抓得住。
# bytes 字面量不能直接放非 ASCII，所以「口令」单独编码后拼进去。
PASSWORD_LITERAL_RE = (rb"(?:password|passwd|pwd|" + "口令".encode("utf-8") +
                       rb")\s*[=:]\s*[\"'][^\"'\n]{8,}[\"']")
DOC_SUFFIXES = (".md", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
                ".sh", ".bat", ".ps1", ".env", ".example", ".txt")

# ── C：本机路径与部署拓扑 ─────────────────────────────────────
PATH_PATTERNS = [
    ("windows-user-home", rb"[A-Za-z]:\\Users\\[A-Za-z0-9_.\-]+"),
    ("posix-home", rb"/home/[A-Za-z0-9_.\-]+/"),
    # 具体安装根目录：文档要写运维手册，就该用 <app> 占位。
    # 写死 /opt/<真实项目名> 等于公开内部目录与服务命名。
    ("prod-install-path", rb"/(?:opt|srv|backup)/[A-Za-z0-9_.\-]+"),
]

# 「口令」这个词本身，用于在 markdown 表格里定位密码列。
PASSWORD_WORD_RE = re.compile(
    (rb"(?i)password|passwd|pwd|")
    + "密码".encode("utf-8") + rb"|" + "口令".encode("utf-8"))

# 合成夹具放行名单。**只对测试文件生效**：
# 早先它对所有文件生效，于是「文档里写了那一行测试账号的密码」也被放行 ——
# 而那正是本仓库事故的形态。域名可以出现在文档里，密码不行。
ALLOW_SUBSTRINGS = (b"@example.com", b"@x.com", b"@x.test", b"@api.example.com")

# 讲「怎么保管秘密」的文档不豁免任何维度。给文档开豁免的实际后果是：
# 事故发生在这个文件里，而它正列在豁免名单上。豁免名单是给不出错的关卡用的。
TEST_PATH_MARKERS = ("/tests/", "test_", ".test.", "/test/")

MAX_FILE_BYTES = 4_000_000


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True)


def tracked_files():
    out = git("ls-files").stdout.decode("utf-8", "replace").splitlines()
    return [f for f in out if f.strip()]


def read(path):
    full = os.path.join(REPO, path)
    if not os.path.isfile(full):
        return None
    try:
        with open(full, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None
    return None if len(data) > MAX_FILE_BYTES else data


def line_of(blob, pos):
    return blob[:pos].count(b"\n") + 1


def is_test_file(path):
    low = "/" + path.replace("\\", "/").lower()
    return any(m in low for m in TEST_PATH_MARKERS)


def line_text_at(blob, pos):
    ls = blob.rfind(b"\n", 0, pos) + 1
    le = blob.find(b"\n", pos)
    return blob[ls:le if le != -1 else len(blob)]


def md_table_password_cells(blob, path):
    """markdown 表格中「密码 / 口令」列里出现反引号字面量。

    这是本仓库事故的**原始形态**：运维手册里有一张「邮箱 | 密码 | …」的表，
    那一列写的是可直接登录的固定口令。事故那一行的原值不抄在这里 ——
    注释里贴原值等于把它从历史清掉之后又写进一个新文件。这条是本轮真踩到的。

    它抓不到的两类，如实写明而不是假装覆盖：
      1. **没有表头的裸数据行**。规则靠表头定位「哪一列是密码」，没有表头
         就不知道该看哪一列。
      2. 表头没写「密码/口令/password」而语义上是密码的列。

    对付 1/2 靠纪律：任何文件（含注释与测试夹具）都不写原值。
    规则是第二道防线，不是第一道。
    """
    hits = []
    # markdown 表格有固定三段结构：表头 / 分隔行 / 数据行。
    # 不按结构走就会出这种错：表头是「函数」，而数据行里的 `hash_password`
    # 含有 "password"，被当成了表头，于是整张表被当成密码表逐行报警。
    # 所以状态必须显式推进：**只有第一行**才有资格当表头。
    state = "idle"   # idle -> header -> data
    pw_col = None
    for idx, line in enumerate(blob.split(b"\n"), 1):
        s = line.strip()
        if not (s.startswith(b"|") and s.endswith(b"|")):
            state = "idle"
            pw_col = None
            continue
        cells = [c.strip() for c in s.strip(b"|").split(b"|")]
        is_sep = bool(cells) and all(set(c) <= set(b"-: ") for c in cells if c)

        if state == "idle":
            if is_sep:
                continue
            state = "header"
            pw_col = None
            for ci, c in enumerate(cells):
                if PASSWORD_WORD_RE.search(c):
                    pw_col = ci
            continue

        if state == "header":
            # 这一行是分隔行，跳过；之后都按数据行处理。
            state = "data"
            continue

        if pw_col is not None and pw_col < len(cells):
            m = re.search(rb"`([^`\n]{4,})`", cells[pw_col])
            if m:
                hits.append((path, idx, m.group(1)[:2] + b"***"))
    return hits


def real_secret_values():
    """从 backend/.env 取真值。返回 (值列表, 状态说明)。"""
    if not os.path.isfile(ENV_PATH):
        return [], "backend/.env 不存在（全新克隆），跳过本维度"
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
                continue  # 只查密钥类；模型名、URL 不是凭据
            values.append((key, val))
    if not values:
        return [], "backend/.env 里没有可判定的密钥类真值，跳过本维度"
    return values, "已载入 %d 个密钥类真值（%s）" % (
        len(values), ", ".join(k for k, _ in values))


def main():
    files = tracked_files()
    failures = []   # (维度, 文件, 行号, 规则)
    notes = []

    print("扫描 %d 个 git 跟踪文件" % len(files))

    # ── A：真值反查 ───────────────────────────────────────────
    secrets, status = real_secret_values()
    notes.append("A 真值反查：%s" % status)
    if secrets:
        for path in files:
            blob = read(path)
            if blob is None:
                continue
            for key, val in secrets:
                needle = val.encode("utf-8", "replace")
                start = 0
                while True:
                    pos = blob.find(needle, start)
                    if pos == -1:
                        break
                    failures.append(("A-真凭据值", path, line_of(blob, pos), key))
                    start = pos + len(needle)

    # ── B / C：形态与路径 ─────────────────────────────────────
    patterns = ([("B-" + n, p) for n, p in SHAPE_PATTERNS] +
                [("C-" + n, p) for n, p in PATH_PATTERNS])
    for path in files:
        blob = read(path)
        if blob is None:
            continue
        allow = is_test_file(path)

        for name, pat in patterns:
            for m in re.finditer(pat, blob):
                # 放行名单按**整行**判定，不按 ±64 字节窗口：
                # 夹具的邮箱常在上一行，窗口判不到就会误报。
                if allow and any(a in line_text_at(blob, m.start())
                                 for a in ALLOW_SUBSTRINGS):
                    continue  # 合成夹具
                failures.append((name, path, line_of(blob, m.start()), "形态/路径"))

        # 口令字面量：只查文档/配置，源码里的密码变量与测试夹具一律不查。
        # **文档不享有任何放行**：域名可以出现在文档里，密码不行。
        lower = path.lower()
        if lower.endswith(DOC_SUFFIXES) or "/docs/" in "/" + lower:
            for m in re.finditer(PASSWORD_LITERAL_RE, blob):
                failures.append(("B-inline-password-literal", path,
                                 line_of(blob, m.start()), "口令字面量（文档/配置）"))

            # 表格密码列：事故的原始形态，靠表头定位而不是靠字面量形态。
            for p2, ln2, redacted in md_table_password_cells(blob, path):
                failures.append(("B-md-table-password-cell", p2, ln2,
                                 "表格密码列字面量 " + redacted.decode("ascii", "replace")))

    # ── 结论行由失败标志推导 ─────────────────────────────────
    for n in notes:
        print("  " + n)
    print("")
    if failures:
        print("FAIL：检出 %d 处敏感内容" % len(failures))
        for dim, path, line, rule in failures:
            print("  %-16s %s:%d  [%s]" % (dim, path, line, rule))
        print("")
        print("处置：值已经落到远端就不是「待定」——先轮换，再清历史，最后才谈补 .gitignore。")
        return 1

    print("PASS：未检出敏感内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
