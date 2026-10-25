"""工单 #55 的变异 harness：证明三个纯函数真被钉住了。

四条变异必须 KILLED，一条反向对照必须保持绿。

注意 ``summarizer.py`` 是**混合行尾**文件（工单 #36 实测过），
所以这里按字节数读出真实行尾做成常量，而不是用 `"\\r\\n" in src` 猜。

工单 #106 把导图清洗规则搬到了 ``backend/mindmap.py``（单一真源），
``summarizer.py`` 只留一层薄委托 ⇒ M3 的锚点跟着搬了家（**锚点要指向
代码现在住的地方**，否则预检会报「0 命中」，那不是变异失败，是锚点过期）。
"""
import io
import subprocess
import sys

from venv_python import venv_python

PY = venv_python()
TARGET = "backend/tests/test_summarizer_pure.py"
SUMMARIZER = "backend/summarizer.py"
DOUYIN = "backend/douyin.py"
MINDMAP = "backend/mindmap.py"


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


NL_S = eol_of(SUMMARIZER)
NL_D = eol_of(DOUYIN)
NL_M = eol_of(MINDMAP)


def n(*lines, nl=NL_S):
    return nl.join(lines)


MUTATIONS = [
    {
        "id": "M1", "expect": "KILLED", "file": SUMMARIZER, "hits": 1,
        "why": "_parse_vtt 不去重：滚动字幕的重复句会原样进结果",
        "find": n('                text = " ".join(text_lines)',
                  "                if text in seen_texts:",
                  "                    continue",
                  "                seen_texts.add(text)"),
        "replace": n('                text = " ".join(text_lines)',
                     "                seen_texts.add(text)"),
    },
    {
        "id": "M2", "expect": "KILLED", "file": SUMMARIZER, "hits": 1,
        "why": "_time_to_seconds 把逗号当分隔符吃掉：静默算错而不是崩",
        "find": n('    parts = ts.replace(",", ".").split(":")'),
        "replace": n('    parts = ts.replace(",", "").split(":")'),
    },
    {
        "id": "M3", "expect": "KILLED", "file": MINDMAP, "hits": 1,
        "why": "首个标题之前的说明文字漏进导图：模型爱写「以下是思维导图：」",
        "find": n("            if stack:", "                stack[-1][1].children.append(block)", "            continue", nl=NL_M),
        "replace": n("            if stack:", "                stack[-1][1].children.append(block)", "            else:", "                roots.append(block)", nl=NL_M),
    },
    {
        "id": "M4", "expect": "KILLED", "file": SUMMARIZER, "hits": 1,
        "why": "_parse_vtt 不剥行内标签",
        "find": n('                    clean = re.sub(r"<[^>]+>", "", line.strip())'),
        "replace": n("                    clean = line.strip()"),
    },
    {
        "id": "R1", "expect": "GREEN", "file": DOUYIN, "hits": 1,
        "why": "反向对照：音频 format_id 与本文件三个纯函数无关",
        "find": n('AUDIO_FORMAT_ID = "douyin_audio"', nl=NL_D),
        "replace": n('AUDIO_FORMAT_ID = "douyin_audio_only"', nl=NL_D),
    },
]

bad = 0
print(f"行尾：summarizer={NL_S!r}  douyin={NL_D!r}")
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