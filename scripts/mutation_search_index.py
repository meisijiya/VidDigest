"""变异确认：门禁第 11 关（社区检索质量）必须真的有牙。

第 11 关的判据是「固定语料上的召回率 + 索引结构」。这类判据最容易出的事
是**悄悄退化**：接口照常返 200，只是搜不出东西了——索引少一列、触发器漏写
一列、分词器被换掉，三者的症状都是这个，而任何「功能正常」的用例都不会红。

按纪律，「跑完是绿的」不能证明关卡有效：必须把每一种要抓的形态故意放回去，
确认它转红，再确认原样恢复。每个用例断言两件事：

  1. 注入后评测脚本退出码非 0
  2. 恢复后文件逐字节等于注入前

第 2 条同样重要：注入动作本身可能把文件写坏，而「恢复失败」在只查退出码时
看不见。

**每个用例都配一条反向对照**：不该管的改动必须保持绿。
守卫越权（该绿的红了）比变异存活更严重。

跑法（Windows 与 Linux 同一条）：

    backend/venv/Scripts/python.exe scripts/mutation_search_index.py
    backend/venv/bin/python scripts/mutation_search_index.py
"""
import importlib.util
import os
import subprocess
import sys

from venv_python import venv_python

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = venv_python()
CHECKER = os.path.join(REPO, "scripts", "eval", "check_search_quality.py")
TARGET = os.path.join(REPO, "backend", "database.py")


def purge_bytecode(path):
    """删掉目标模块的缓存字节码。

    为什么必须删：Python 判断一个 `.pyc` 还能不能用，只看两样东西——源文件的
    mtime（**秒级**）与源文件的大小。本脚本的注入里有**长度不变**的那一条
    （反向对照只改了一个数字），而注入与还原又发生在同一秒里。于是 pyc 头里
    记录的 mtime 与 size 跟还原后的源文件严丝合缝，Python 认定这个 pyc 有效，
    实际加载的却是注入期编译出来的字节码。

    症状不看源码看不出来：文件明明还原了，测试却读到被改过的常量。
    所以每次注入前与还原后都清一次，逼 Python 重新编译。
    """
    cache = importlib.util.cache_from_source(path)
    try:
        os.remove(cache)
    except FileNotFoundError:
        pass
    return cache

# (用例名, 旧字节锚点, 新字节, 期望评测脚本失败)
#
# 锚点必须是**逐字节**的：只替换整行/整块，不做「模糊匹配」。
# 锚点没命中 = 装置失败（不是守住了），脚本会把它记成不合格。
CASES = [
    (
        "M1 索引列退回三列：正文不进索引",
        b'_VIDEO_SEARCH_COLUMNS = ("video_title", "tags", "video_url", '
        b'"summary_md", "subtitle_text")',
        b'_VIDEO_SEARCH_COLUMNS = ("video_title", "tags", "video_url")',
        True,
    ),
    (
        "M2 触发器只写三列：索引与触发器不同步",
        b"    CREATE TRIGGER IF NOT EXISTS videos_fts_ai AFTER INSERT ON videos BEGIN\n"
        b"        INSERT INTO {_VIDEO_SEARCH_INDEX}(rowid, {_SEARCH_INDEX_COLS})\n"
        b"        VALUES (new.id, {_SEARCH_INDEX_NEW_VALS});",
        b"    CREATE TRIGGER IF NOT EXISTS videos_fts_ai AFTER INSERT ON videos BEGIN\n"
        b"        INSERT INTO {_VIDEO_SEARCH_INDEX}(rowid, video_title, tags, video_url)\n"
        b"        VALUES (new.id, new.video_title, new.tags, new.video_url);",
        True,
    ),
    (
        "M3 分词器换掉：中文子串召不回",
        b'_VIDEO_SEARCH_TOKENIZER = "trigram"',
        b'_VIDEO_SEARCH_TOKENIZER = "ascii"',
        True,
    ),
    (
        "M4 反向对照：与检索无关的改动必须保持绿",
        b"MAX_PARSE_HISTORY_PER_USER = 1000",
        b"MAX_PARSE_HISTORY_PER_USER = 1001",
        False,
    ),
]


def run_checker():
    # 清掉缓存字节码再跑：否则 Python 可能加载上一轮注入期编译出来的 .pyc，
    # 让「已还原的源码」读起来还是被改过的值。
    purge_bytecode(TARGET)
    p = subprocess.run([PY, CHECKER], capture_output=True, cwd=REPO)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace") + (
        p.stderr or b""
    ).decode("utf-8", "replace")


print("=" * 70)
print("变异确认：第 11 关（社区检索质量）是否有牙")
print("=" * 70)

fails = []

rc, out = run_checker()
print("\n[基线] 评测脚本 退出码=%d  期望=0  -> %s"
      % (rc, "OK" if rc == 0 else "BAD"))
if rc != 0:
    fails.append("基线就已经是红的：评测脚本跑不过，先修基线")
    print(out[-3000:])

with open(TARGET, "rb") as fh:
    original = fh.read()

for name, old, new, expect_fail in CASES:
    if original.count(old) != 1:
        fails.append("%s：锚点在源码里出现 %d 次（必须恰好 1 次，装置失败不是守住了）"
                     % (name, original.count(old)))
        print("\n[BAD] %s" % name)
        print("     锚点命中次数=%d（期望 1）" % original.count(old))
        continue
    try:
        with open(TARGET, "wb") as fh:
            fh.write(original.replace(old, new, 1))
        purge_bytecode(TARGET)
        rc, out = run_checker()
        verdict = "OK" if ((rc != 0) == expect_fail) else "BAD"
        if verdict == "BAD":
            fails.append("%s（期望 fail=%s，实得 rc=%d）" % (name, expect_fail, rc))
        print("\n[%s] %s" % (verdict, name))
        print("     期望 fail=%s，实得 rc=%d" % (expect_fail, rc))
        for ln in out.splitlines():
            if ln.startswith("<FAIL>"):
                print("     关卡指认: %s" % ln.strip())
    finally:
        with open(TARGET, "wb") as fh:
            fh.write(original)
        purge_bytecode(TARGET)
        with open(TARGET, "rb") as fh:
            restored = fh.read()
        same = restored == original
        if not same:
            fails.append("%s：恢复后文件与注入前不一致（注入动作写坏了文件）" % name)
        print("     恢复: %s" % ("逐字节一致" if same else "!!! 不一致 !!!"))

rc, out = run_checker()
print("\n[收尾] 评测脚本 退出码=%d  期望=0  -> %s"
      % (rc, "OK" if rc == 0 else "BAD"))
if rc != 0:
    fails.append("收尾不是绿的，说明有残留注入")
    print(out[-3000:])

print("")
print("=" * 70)
if fails:
    print("RESULT: 关卡不可信，共 %d 项不合格" % len(fails))
    for f in fails:
        print("   - " + f)
    sys.exit(1)
print("RESULT: 每个注入形态都按预期转红，恢复后逐字节一致，基线与收尾均为绿")
sys.exit(0)
