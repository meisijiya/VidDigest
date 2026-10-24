"""变异脚本：证明「下载产物治理」那批测试真能杀掉缺陷（工单 #90）。

逐个破坏 → 跑指定测试 → 必须转红，而且必须是**断言**转红。绿灯不算证据。

## 为什么值得单独守

「用完即删」是一串**看不见**的约定：清理要挂在响应发完之后、失败路径
要自己清半截文件、清理的范围只能是「这次下载自己写下的路径」、断连
也要清。这些约定写错了，功能测试可能照样全绿——文件多留一会儿没人
看见，0 字节响应要到用户那边才暴露。

所以只能靠「故意打坏 + 看它有没有变红」来证明守得住：测试写完不代表
它在守，只代表它在**绿**。

## 判据：KILLED 必须伴随 AssertionError

语法错误、导入失败、模块炸掉——这些会让测试「红」，但它们证明的是
「我改坏了代码」，不是「这条用例在守」。所以脚本额外看输出里有没有
`AssertionError`：没有就当这条用例没守住。

## 每条变异钉住一个具体后果

| 变异 | 现实里的后果 |
| --- | --- |
| M1 | 清理挂早一步，响应发出去时文件已经没了 |
| M2 | 文件永远留在盘上，磁盘慢慢被吃光 |
| M3 | 下载失败留下半截文件，占着地方没人管 |
| M4 | 上一台机器的残留被打进镜像 |
| M5 | 巡检对下载目录占用彻底失明 |
| M6 | yt-dlp 的半截文件认不出来，没人清 |
| M7 | 巡检在转码还没结束时把文件删了 |
| M8 | 按时间窗口扫目录来清，把别的请求的在途文件删了 |
| M9 | 清理时不比身份，把别人的同名产物删了 |
| M10 | 清理挂在「响应发完」那条路上，断连时不删 |
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
MAIN_FILE = REPO / "backend" / "main.py"
ARTIFACTS_FILE = REPO / "backend" / "download_artifacts.py"
DOWNLOADER_FILE = REPO / "backend" / "downloader.py"
DOCKERIGNORE_FILE = REPO / "backend" / ".dockerignore"
INSPECT_FILE = REPO / "scripts" / "ops" / "inspect_runtime.py"
TARGET = ["tests/test_download_artifacts.py", "tests/test_inspect_download_dir.py"]

#: (说明, 目标文件, 模式串, 替换串)
MUTATIONS = [
    (
        "M1 清理挪到了响应发出之前：文件在响应生成前就没了",
        ARTIFACTS_FILE,
        "        try:\n"
        "            await super().__call__(scope, receive, send)\n",
        "        try:\n"
        "            remove_artifact(self.path, self._identity)\n"
        "            await super().__call__(scope, receive, send)\n",
    ),
    (
        "M2 成功路径根本不删：产物永远留在盘上",
        ARTIFACTS_FILE,
        "            remove_artifact(self.path, self._identity)\n",
        "            pass  # 变异：成功路径不再清理\n",
    ),
    (
        "M3 失败路径不清半截文件：网络断了就留下半个文件",
        DOWNLOADER_FILE,
        "            written.remove_all()\n",
        "            pass  # 变异：失败路径不再清理\n",
    ),
    (
        "M4 构建上下文不再排除下载目录：残留被打进镜像",
        DOCKERIGNORE_FILE,
        "downloads/",
        "",
    ),
    (
        "M5 下载目录占用阈值变成 0：巡检对磁盘占用彻底失明",
        INSPECT_FILE,
        "DEFAULT_MAX_DOWNLOAD_MB = 2048",
        "DEFAULT_MAX_DOWNLOAD_MB = 0",
    ),
    (
        "M6 不再认正在写的临时文件：yt-dlp 的半截文件没人清",
        ARTIFACTS_FILE,
        "        tmp = status.get(\"tmpfilename\")\n"
        "        if tmp:\n"
        "            self._paths.add(tmp)\n",
        "        tmp = status.get(\"tmpfilename\")\n"
        "        if tmp:\n"
        "            pass  # 变异：不再认正在写的临时文件\n",
    ),
    (
        "M7 超龄下限被放开：巡检会在转码还没结束时删文件",
        INSPECT_FILE,
        "if hours < 1:",
        "if hours < 0:",
    ),
    (
        "M8 失败清理退化成按时间窗口扫目录：别的请求的在途文件被删了",
        DOWNLOADER_FILE,
        "        except Exception:\n"
        "            # 下载失败：把这次下载自己写下的半截文件清掉。\n"
        "            # 抖音那条通道也这么干（见 `backend/douyin.py`），\n"
        "            # 两边共用同一份清理口径（`backend/download_artifacts.py`）。\n"
        "            written.remove_all()\n"
        "            raise\n",
        "        except Exception:\n"
        "            import time\n"
        "            cutoff = time.time() - 1.0\n"
        "            for name in os.listdir(self.DOWNLOAD_DIR):\n"
        "                path = os.path.join(self.DOWNLOAD_DIR, name)\n"
        "                try:\n"
        "                    if os.path.getmtime(path) >= cutoff:\n"
        "                        os.remove(path)\n"
        "                except OSError:\n"
        "                    pass\n"
        "            raise\n",
    ),
    (
        "M9 清理时不比身份：把别人的同名产物删了",
        ARTIFACTS_FILE,
        "    if identity is not None and file_identity(path) != identity:\n",
        "    if False:\n",
    ),
    (
        "M10 清理挂在「响应发完」那条路上：客户端断开时不删",
        ARTIFACTS_FILE,
        "    async def __call__(self, scope, receive, send):\n"
        "        try:\n"
        "            await super().__call__(scope, receive, send)\n"
        "        finally:\n"
        "            # 删不掉只记日志，不影响响应：这时响应要么已经发出去了，\n"
        "            # 要么正在失败中，把清理的失败变成用户看到的错误是拿小错换大错。\n"
        "            remove_artifact(self.path, self._identity)\n",
        "    async def __call__(self, scope, receive, send):\n"
        "        await super().__call__(scope, receive, send)\n"
        "        # 删不掉只记日志，不影响响应：这时响应要么已经发出去了，\n"
        "        # 要么正在失败中，把清理的失败变成用户看到的错误是拿小错换大错。\n"
        "        remove_artifact(self.path, self._identity)\n",
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
        MAIN_FILE: read_source(MAIN_FILE),
        ARTIFACTS_FILE: read_source(ARTIFACTS_FILE),
        DOWNLOADER_FILE: read_source(DOWNLOADER_FILE),
        DOCKERIGNORE_FILE: read_source(DOCKERIGNORE_FILE),
        INSPECT_FILE: read_source(INSPECT_FILE),
    }
    survived, not_run = [], []

    try:
        for name, target_file, pattern, replacement in MUTATIONS:
            original = originals[target_file]
            mutated = mutate_bytes(original, pattern, replacement)
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
        "\n还原自检: main.py / download_artifacts.py / downloader.py / "
        ".dockerignore / inspect_runtime.py 逐字节一致"
    )

    print()
    if survived or not_run:
        print(f"结论: {len(survived)} 个存活 / {len(not_run)} 个无效 —— 产物治理的测试有缺口")
        for s in survived:
            print(f"  - 存活: {s}")
        for s in not_run:
            print(f"  - 无效: {s}")
        return 1
    print(f"结论: {len(MUTATIONS)} 个变异全部被杀死，产物治理守得住")
    return 0


if __name__ == "__main__":
    sys.exit(main())
