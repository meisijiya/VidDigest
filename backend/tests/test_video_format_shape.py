r"""视频 format 字典的**结构守卫 · 收口后形态**（工单 #43 建立，#44 改写）。

背景
----
这个字典原先在四处各写一遍 14 行的字面量。#43 先给它加了守卫并顺手删掉
唯一一处形状差异（`_direct_url`，写 1 处读 0 处）。#44 用那个守卫当安全网
做了收口，于是**四条构造点变成一处定义 + 四次调用**。

守卫随之换形态
--------------
收口前它断的是「**所有**字面量构造点的键集合相同」——那条判据在只有一处
定义之后**恒真**，留着等于不设防。所以改成断「**不该再有第二处定义**」：

1. 不许再有含 `format_id` 的字面量字典（有人手写回去就红）。
2. `make_format(` 的调用点**恰好 4 处**——少一处 = 那个平台的格式选项消失。
3. `make_format()` 返回的键集合**恰好等于**收口前钉住的那 14 个。
4. `UI_READ_KEYS` ⊆ `make_format()` 的键集（界面与后端的契约）。
5. 输出里不许有下划线开头的键（`_direct_url` 的直接教训）。

⚠️ **第 1 条与第 2 条必须都断**，只断一条会漏：
   只断「无字面量」→ 有人把某处整个删掉，守卫全绿而那个平台的选项消失；
   只断「4 处调用」→ 有人把其中一处改回字面量，数字对上了而形状又分叉。

**为什么 `UI_READ_KEYS` 必须手写**
------------------------------
派生则判据恒为真：从 `make_format()` 的键集扫出来的「界面读什么」，那么
「构造包含构造」永远成立，一条也拦不住。真实读出口在
`frontend/src/components/VideoResult.vue`——**另一个语言**，Python 侧
观察不到，所以这里手写。
"""
import ast
from pathlib import Path

import formats

BACKEND = Path(__file__).resolve().parent.parent

#: 界面**真正读**的键。来源 frontend/src/components/VideoResult.vue。
#: 手写，不派生——理由见模块 docstring。
UI_READ_KEYS = frozenset({
    "format_id",   # :key 与 emit('download', …)
    "kind",        # 画质块 / 音频块 分流，以及按钮文案
    "label",       # 选项按钮上的文字
    "resolution",  # 画质标题
    "abr",         # 音频标题（无分辨率时）
    "ext",         # 音频标题的兜底
})

#: 收口前 #43 在四处构造点实测到的键集合。它是这次收口的**验收基线**：
#: 收口是纯重构，这 14 个键一个不多一个不少。
EXPECTED_KEYS = frozenset({
    "format_id", "ext", "resolution", "height", "width",
    "filesize", "filesize_approx", "vcodec", "acodec", "abr",
    "has_video", "has_audio", "kind", "label",
})

#: 构造 format 的两个模块。别把扫描扩到全 backend。
SOURCES = ("douyin.py", "downloader.py")


def _literal_format_sites(tree):
    """含 ``format_id`` 的**字面量** dict（含 ``**`` 展开的那种）。

    收口之后这里应当是空的。它不为空 = 有人绕过 ``make_format`` 手写了。
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if "format_id" in keys:
            out.append((node.lineno, frozenset(keys)))
    return out


def _make_format_calls(tree):
    """``make_format(...)`` 的调用点行号。"""
    return sorted(n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)
                  and n.func.id == "make_format")


def _make_format_call_nodes(tree):
    """``make_format(...)`` 的调用节点。"""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "make_format"]


LITERAL_SITES = []
CALL_SITES = []
for _name in SOURCES:
    _src = (BACKEND / _name).read_text(encoding="utf-8")
    _tree = ast.parse(_src)
    # `make_format(**{...})` 里那个 dict 是**合法的**覆盖集，不是绕过。
    # 它在 AST 里仍是 ast.Dict，直接扫会把它误判成「手写了一份」。
    # 所以先把这类 dict 的节点 id 收起来，排除掉。
    _inside = set()
    for _call in _make_format_call_nodes(_tree):
        # `**{...}` 在 AST 里是 **keyword（arg=None）** 的值，不是位置参数。
        # 只扫 args 会漏掉它，于是把合法的覆盖集误判成「手写了一份」。
        for _arg in list(_call.args) + [k.value for k in _call.keywords
                                        if k.arg is None]:
            if isinstance(_arg, ast.Dict):
                _inside.add(id(_arg))
    for node in ast.walk(_tree):
        if isinstance(node, ast.Dict) and id(node) not in _inside:
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "format_id" in keys:
                LITERAL_SITES.append((_name, node.lineno))
    CALL_SITES += [(_name, n.lineno) for n in _make_format_call_nodes(_tree)]


def test_no_bypasses_make_format():
    """① 收口之后，**不许再有**含 `format_id` 的字面量字典。

    与 ② 一起断才有意义：只断这一条，「有人把某处整个删掉」是绿的。
    """
    assert not LITERAL_SITES, (
        f"这些地方绕过 make_format 手写了 format 字面量字典：{LITERAL_SITES}。"
        "键集合在单一出处（formats.FORMAT_DEFAULTS）里定义才是一处；"
        "手写回去等于把工单 #44 收掉的东西又放回来。")


def test_all_four_call_sites_are_still_there():
    """② 恰好 5 处调用：4 处原始构造 + 1 处「视频+音频合并」的派生。

    「扫不到」和「扫到 6 处」同样是坏消息：少一处 = 那个平台的格式选项消失，
    多一处 = 有人在别处也造了一份，两边会漂。
    """
    assert len(CALL_SITES) == 5, (
        f"应当恰好 5 处 make_format( 调用，实得 {len(CALL_SITES)}：{CALL_SITES}。"
        "四处原始构造是 yt-dlp 音频 / yt-dlp 视频 / 抖音视频 / 抖音音频，"
        "第五处是「所有视频格式都没音频时」的合并项（它展开 videos[0]，"
        "所以也走 make_format）。")


def test_key_set_matches_what_was_measured_before_the_refactor():
    """③ 收口是纯重构：键集合一个不多一个不少。"""
    got = frozenset(formats.make_format())
    assert got == EXPECTED_KEYS, (
        f"make_format() 的键集合与收口前的实测不一致。\n"
        f"  多了：{sorted(got - EXPECTED_KEYS)}\n"
        f"  少了：{sorted(EXPECTED_KEYS - got)}\n"
        "收口本该是行为无关的重构——键一变就不是了。")


def test_every_call_site_only_uses_known_keys():
    """④ 每个调用点的关键字都必须在 FORMAT_DEFAULTS 里。

    这一条同时覆盖「拼错的键名」：写错一个键，`make_format` 当场抛错，
    而守卫会告诉你它是在哪一处抛的。
    """
    for _name, _ln in CALL_SITES:
        _tree = ast.parse((BACKEND / _name).read_text(encoding="utf-8"))
        for node in ast.walk(_tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "make_format"
                    and node.lineno == _ln):
                # `**x` 这种解包的 keyword.arg 是 None，不是键名。
                # 合并项走的就是这条路，不滤掉会被当成「未知键」。
                used = {k.arg for k in node.keywords if k.arg is not None}
                spreads = [k for k in node.keywords if k.arg is None]
                assert used <= set(formats.FORMAT_DEFAULTS), (
                    f"{_name}:{_ln} 传了未知键 {sorted(used - set(formats.FORMAT_DEFAULTS))}；"
                    "未知的键会在运行时抛错而不是被忽略")
                assert len(spreads) <= 1, (
                    f"{_name}:{_ln} 用了 {len(spreads)} 个 ** 展开，只该有一个")
                assert node.args == [], (
                    f"{_name}:{_ln} 用了位置参数 —— 键名只能写字面量，"
                    "位置参数会让「这个键叫什么」不可读")


def test_ui_read_keys_are_all_produced():
    """⑤ 界面读的 6 个键，每一个都必须由 make_format 产出。"""
    missing = sorted(UI_READ_KEYS - set(formats.make_format()))
    assert not missing, (
        f"make_format() 不产出界面要读的键 {missing}。"
        "`VideoResult.vue` 读到 undefined，界面不报错——"
        "而这正是「按数据在哪张表枚举」会漏掉的那一半。")


def test_unknown_key_raises_instead_of_adding_a_field():
    """⑥ 未知键抛错，不是静默新增。

    收口的**真正收益**在这里：原先写错一个键名，字典里就多出一个
    没有任何人读的字段，而所有形状检查都还绿。
    """
    try:
        formats.make_format(filesize_apporx=1)
    except KeyError:
        return
    raise AssertionError(
        "make_format(filesize_apporx=1) 没有抛错 —— "
        "打错的键名被静默塞进了字典，成了一个没人读的字段")


def test_no_private_looking_key_crosses_the_wire():
    """⑦ 下划线开头的键不该出现在要发给前端的 format 里。

    这条是 `_direct_url` 的直接教训：私有约定靠前缀表达是廉价的，
    但前缀不会自己拦人。
    """
    private = sorted(k for k in formats.make_format() if k.startswith("_"))
    assert not private, f"make_format() 的默认键里有以下划线开头的 {private}"


def test_ui_read_keys_are_written_down_here_not_derived():
    """⑧ 反查表：那份手写清单不许被改成从 make_format 派生。"""
    assert UI_READ_KEYS == frozenset({
        "format_id", "kind", "label", "resolution", "abr", "ext",
    }), (
        "UI_READ_KEYS 变了。它应当与 frontend/src/components/VideoResult.vue "
        "实际读的键逐字相等；若界面改了读法，同步改这里并说明为什么")
    assert "format_id" in UI_READ_KEYS, "至少要有 emit('download') 用的那个"