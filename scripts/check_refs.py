#!/usr/bin/env python
"""引用一致性检查：说明性文本里指着的东西必须真的存在。

**为什么这道关卡存在**：本仓库的规约（AGENTS.md）、设计决策（docs/adr/）、
测试策略（docs/TESTING-STRATEGY.md）与源码注释之间**互相指着**。
指着的那一头被改写或删掉时，**没有任何一条既有用例会红**——
用例断言的是行为，不是「你写的这句话还指得对」。

这不是假设。`frontend/src/App.vue` 里那句「不引 vue-router（ADR 0010）」
就指着错的那一篇：ADR 0010 记的是管理后台范围与鉴权，
「不引 vue-router」这件事在 docs/adr/ 下从来没有被记过。
文档与注释腐烂之后**仍在产生约束力**（AGENTS.md 工作规则第 6 条），
而它们自己不会报警。这道关卡让「腐烂」变成「门禁红」。

**查两类引用**：

1. ADR 编号——文里出现 `ADR NNNN`，docs/adr/ 下必须有一个编号为 NNNN 的文件。
2. 仓库内路径锚点——文里出现 `backend/…`、`docs/…` 这类路径，被指的文件必须存在；
   **代码文件**里的行号锚点还要满足「行号 ≤ 目标文件行数」。

**只查说明性文本**（文档全文、代码注释与 docstring）：
代码里的字符串字面量与命令参数**不查**——那种路径写错时代码自己会当场炸
（`open()` 抛 FileNotFoundError，`git add` 报 pathspec 错误），
而它们不是「人读到的意图」，不会腐烂成误导性的说明。
反过来，把判据放宽到「代码里出现的一切路径都查」，只会逼着检查器去理解
哪些字面量是路径、哪些是参数——那是让判据变噪声。

**不查什么**（这些不是「指着某个具体东西」）：外部 URL、绝对路径、
含 `*` 的 glob、含占位符的写法、以及本机环境产物（venv、node_modules 等）。
"""
import ast
import io
import os
import re
import subprocess
import sys
import tokenize

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 会读的文本后缀
TEXT_SUFFIXES = (
    ".py", ".vue", ".js", ".mjs", ".ts", ".sh", ".md", ".yml", ".yaml",
    ".json", ".txt", ".cfg", ".toml", ".html", ".css", ".sql",
)

# 只有这几个根下的路径被认成「仓库内路径锚点」
PATH_ROOTS = ("backend", "frontend", "docs", "scripts")

# 仓库根下的单文件锚点
PATH_TOP_FILES = ("AGENTS.md", "GLOSSARY.md", "README.md", "init.sh")

# 本机环境产物：不随仓库走，所以不查
#
# 前四项是工具链目录；后四项是**运行期产物**：data/ 是本地库与下载缓存，
# logs/ 是运行期日志，backups/ 是备份档，downloads/ 是下载缓存。
# 它们都不在版本库里——文档里写「数据在 backend/data/app.db」是在描述
# 运行期状态，不是在指一个仓库内的文件。按文件存在性去查它，
# 会让「本机碰巧有」变成「门禁绿」的理由：换一台机器、换一个干净克隆，
# 同样的文档就该转红。那是误报。
IGNORED_SEGMENTS = (
    "venv", ".venv", "env", "node_modules", "__pycache__",
    ".git", "dist", "build", ".pytest_cache", ".idea", ".vscode",
    "data", "logs", "backups", "downloads",
)

# 代码文件：行号锚点也查边界
CODE_SUFFIXES = (".py", ".vue", ".js", ".mjs", ".ts", ".sh")

ADR_RE = re.compile(r"ADR\s*(\d{4})")

# docs/adr/0001 这种写法指的是「编号 0001 这一篇 ADR」，不是文件名
ADR_SHORTHAND_RE = re.compile(r"^docs/adr/(\d{4})$")

ANCHOR_RE = re.compile(
    r"(?P<path>(?:backend|frontend|docs|scripts)/[A-Za-z0-9_./-]+"
    r"|AGENTS\.md|GLOSSARY\.md|README\.md|init\.sh)"
    r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?"
)

# 仓库里有中文名的文件。上面的正则只吃 ASCII 字符，于是会把一个中文文件名
# 截成只剩 ASCII 前缀（路径就少了一截），再报一条「路径不存在」——那是误报。
# 判据：把后面这串中文（连同它的扩展名）并进去之后，如果真存在，
# 就以并进去的为准。
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+(?:\.[A-Za-z0-9]+)?")


def tracked_files():
    """取版本管理里的文件清单。

    只查跟踪文件：未跟踪的文件（本地 handoff、临时产物）不该让门禁红。
    """
    proc = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True)
    if proc.returncode != 0:
        sys.stderr.write("ERROR: git ls-files 失败，无法确定检查范围。\n")
        sys.exit(2)
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def read_text(rel):
    path = os.path.join(REPO, rel)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (UnicodeDecodeError, OSError):
        return None


def has_ignored_segment(rel):
    parts = rel.replace("\\", "/").split("/")
    return any(p in IGNORED_SEGMENTS for p in parts)


def git_ignored(paths):
    """问 git：这些路径里哪些被 .gitignore 排除。

    运行期产物（backend/.env、backend/data/app.db、backend/logs/app.log、
    backend/backups/*.db）都不在版本库里。文档里写它们是在描述**运行期状态**，
    不是在指一个仓库内的文件；按文件存在性去查它们，会让「本机碰巧有」
    变成「门禁绿」的理由——换一台机器、换一个干净克隆，同样的文档就该转红。
    那是误报，而且是 2026-10-23 在 CI 上第一次暴露的（CI 没有 backend/.env）。

    所以判据交给 .gitignore：被排除的不查，其余照查。
    放行规则也由 .gitignore 自己说：`.env*` 被排除，但 `!.env.example`
    放行模板，所以 .env.example 仍然要查。
    """
    uniq = sorted({p for p in paths if p})
    if not uniq:
        return set()
    payload = ("\n".join(uniq) + "\n").encode("utf-8")
    proc = subprocess.run(
        ["git", "-C", REPO, "check-ignore", "--stdin"],
        input=payload,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        sys.stderr.write("ERROR: git check-ignore 失败，无法判断运行期产物。\n")
        sys.exit(2)
    return {p for p in proc.stdout.decode("utf-8", "replace").splitlines() if p}


def existing_adr_ids():
    adr_dir = os.path.join(REPO, "docs", "adr")
    ids = set()
    if not os.path.isdir(adr_dir):
        return ids
    for name in os.listdir(adr_dir):
        m = re.match(r"^(\d{4})-", name)
        if m:
            ids.add(m.group(1))
    return ids


def explanatory_lines(rel, text):
    """只取「说明性文本」：文档全文、代码注释与 docstring。

    返回 [(行号, 文本)]。代码里的普通字符串字面量与命令参数不在其中——
    理由见模块 docstring。
    """
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".md":
        return [(i, line) for i, line in enumerate(text.splitlines(), 1)]

    if ext == ".py":
        out = []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    out.append((tok.start[0], tok.string))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Module, ast.ClassDef,
                                         ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = getattr(node, "body", None)
                if not body:
                    continue
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    out.append((first.lineno, first.value.value))
        return out

    out = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if (stripped.startswith("//") or stripped.startswith("#")
                or stripped.startswith("*") or stripped.startswith("/*")
                or stripped.startswith("<!--")):
            out.append((i, line))
    return out


def main():
    violations = []
    adr_ids = existing_adr_ids()
    files = tracked_files()
    checked = 0
    candidates = []

    for rel in files:
        if not rel.lower().endswith(TEXT_SUFFIXES):
            continue
        if has_ignored_segment(rel):
            continue
        text = read_text(rel)
        if text is None:
            continue
        checked += 1
        is_code = rel.lower().endswith(CODE_SUFFIXES)

        for num, line in explanatory_lines(rel, text):
            for m in ADR_RE.finditer(line):
                if m.group(1) not in adr_ids:
                    violations.append(
                        "%s:%d: ADR %s 不存在（docs/adr/ 下没有这一号）"
                        % (rel, num, m.group(1))
                    )

            for m in ANCHOR_RE.finditer(line):
                path = m.group("path")
                tail = line[m.end():m.end() + 1]
                if tail and tail in "*…{<":
                    # glob 或占位符：不是指着某个具体东西
                    continue
                before = line[max(0, m.start() - 40):m.start()]
                if "://" in before:
                    # URL 里的路径片段
                    continue
                if path.endswith("/"):
                    # 只是前缀根，不是文件锚点
                    continue
                norm = path.rstrip("/")
                if has_ignored_segment(norm):
                    continue

                # 中文文件名：先把紧跟其后的一串中文并进来试一次。
                # 并进去之后存在 → 以它为准（base 只是被 ASCII 边界截断）；
                # 不存在 → 仍按 base 判，但报错时写并进去的版本，
                # 这样报出来的是作者真正写的东西。
                shown = path
                cjk = CJK_RUN_RE.match(line, m.end())
                if cjk:
                    extended = norm + cjk.group(0)
                    shown = extended
                    if os.path.exists(os.path.join(REPO, extended)):
                        norm = extended

                shorthand = ADR_SHORTHAND_RE.match(norm)
                if shorthand:
                    if shorthand.group(1) not in adr_ids:
                        violations.append(
                            "%s:%d: ADR %s 不存在（docs/adr/ 下没有这一号）"
                            % (rel, num, shorthand.group(1))
                        )
                    continue

                # 存在性与行号边界统一放到扫描之后判：先收集所有候选路径，
                # 再一次性问 git 哪些被 .gitignore 排除（运行期产物不查）。
                candidates.append((rel, num, norm, shown, m.group("start"), is_code))

    ignored = git_ignored({c[2] for c in candidates})
    for rel, num, norm, shown, start, is_code in candidates:
        if norm in ignored:
            continue
        target = os.path.join(REPO, norm)
        if not os.path.exists(target):
            violations.append("%s:%d: 路径不存在：%s" % (rel, num, shown))
            continue
        if not (is_code and start and os.path.isfile(target)):
            continue
        target_text = read_text(norm)
        if target_text is None:
            continue
        total_lines = len(target_text.splitlines())
        if total_lines and int(start) > total_lines:
            violations.append(
                "%s:%d: 行号越界：%s 只有 %d 行" % (rel, num, norm, total_lines)
            )

    if violations:
        print("refs: %d 处引用失配" % len(violations))
        for v in violations:
            print("  " + v)
        sys.exit(1)

    print("refs: OK（%d 个跟踪文件，0 处失配）" % checked)


if __name__ == "__main__":
    main()
