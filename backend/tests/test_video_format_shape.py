r"""视频 format 字典的**结构守卫**（工单 #43）。

背景
----
顺着工单 #37（`extract()` 的 7 键形状收口）重问同一族的问法：format 字典
有没有单一出处？答案是没有——**四处字面量各写一遍**，而且**已经出现过一次
形状不一致**：`douyin.py:340` 的抖音视频那一处比另外三处多一个 `_direct_url`。

    douyin.py:340    15 键   ← 多一个 _direct_url（写 1 处、读 0 处、测 0 处）
    douyin.py:364    14 键
    downloader.py:143 14 键
    downloader.py:180 14 键
    downloader.py:206 14 键   ← `{**best_video, ...}`，展开已有字典，不是独立构造

`_direct_url` 是抖音下载看起来「应该有直链」时留下的残留字段：实际下载走
`main.py:141-150` 的 mode 分发（`AUDIO_FORMAT_ID` → audio，否则 video），
从没用过它。所以它不是「漏掉的出口」，是没人读的字段——**而它恰好是那唯一
一处形状差异的来源**。

**当前没有用户可见症状**，这一点写在前面：界面只读的 6 个键在四处构造里
都在。这份守卫守的不是「现在坏了」，是**下一次有人只改其中一处**——
症状会是某个平台上某几个字段渲染成 `undefined` 而界面不报错。

判据
----
1. **形状全等**：所有含 `format_id` 的字面量字典构造点，键集合完全相同。
2. **契约守卫**：`UI_READ_KEYS` 是**手写常量**，不由扫描结果派生。
   每一处构造都必须包含它——界面少读一个键时，界面只是少显示一样东西。
3. **派生点只能覆盖已有键**：`{**base, ...}` 那处不得引入新键名。
   它引入的键基类没有，基类那一支的平台就会缺字段。

为什么 `UI_READ_KEYS` 必须手写
--------------------------
派生则判据恒为真：从构造点扫出来的键集当「界面读什么」，那么
「构造点包含构造点」永远成立，一条也拦不住。真实的读出口在
`frontend/src/components/VideoResult.vue`（`format_id` / `kind` / `label` /
`resolution` / `abr` / `ext`）——那是**另一个语言**，Python 侧观察不到，
所以这里手写并在前端侧另有一条断言。
"""
import ast
from pathlib import Path

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

#: 构造 format 字典的两个模块。别把扫描扩到全 backend：
#: `api_*.py` 里那些是**别的**字典，`model_catalog.py` 里那批是模型条目。
SOURCES = ("douyin.py", "downloader.py")


def _dict_sites(tree):
    """把 AST 里所有字面量 dict 分成两类。

    返回 ``(构造点, 派生点)``：

    - 构造点：全部键都是字符串常量。没有 ``**`` 解包。
    - 派生点：含 ``**something`` 解包（键位是 ``None``）。它**继承**基类形状，
      自己只覆盖列出的那几个键——所以不能当独立构造点计数，
      但它覆盖的键必须是基类已有的，否则基类那一支的平台会缺字段。
    """
    built, derived = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        explicit, unpacked = [], False
        for k, v in zip(node.keys, node.values):
            if k is None:                      # **base
                unpacked = True
                continue
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                explicit.append(k.value)
            else:
                explicit = None
                break
        if explicit is None:
            continue
        if "format_id" not in explicit:
            continue
        (derived if unpacked else built).append(
            (node.lineno, frozenset(explicit)))
    return built, derived


def _collect():
    built, derived = [], []
    for name in SOURCES:
        path = BACKEND / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        b, d = _dict_sites(tree)
        built += [(name, ln, keys) for ln, keys in b]
        derived += [(name, ln, keys) for ln, keys in d]
    return built, derived


BUILT, DERIVED = _collect()


def test_we_actually_found_the_construction_sites():
    """夹具层自检：扫不到东西时，下面全部断言都会**恒真**。

    这不是形式主义——工单 #38 的第一版探针就因为只扫 `For`/`Assign`
    而漏掉了 pydantic 字段声明与 `return {...}`，扫出来的「无违规」
    什么也没说。所以先把「扫到了几个」钉住。
    """
    assert len(BUILT) == 4, (
        f"应当扫到 4 处 format 构造点，实得 {len(BUILT)}："
        f"{[(f, ln) for f, ln, _ in BUILT]}。"
        "扫不到 = 下面所有断言恒真 = 守卫失效。"
        "若确实改了构造点数量，同步更新这里**并说明为什么**")
    assert len(DERIVED) == 1, (
        f"应当扫到 1 处 `{{**base}}` 派生点，实得 {len(DERIVED)}："
        f"{[(f, ln) for f, ln, _ in DERIVED]}")


def test_all_construction_sites_have_the_same_keys():
    """核心判据：形状全等。多一个键 = 某个平台会多一个字段，少一个 = 少一个。"""
    shapes = {}
    for name, ln, keys in BUILT:
        shapes.setdefault(keys, []).append(f"{name}:{ln}")
    assert len(shapes) == 1, (
        "format 字典的键集合在各构造点之间**不一致**：\n"
        + "\n".join(f"  {len(k):2d} 键 @ {', '.join(v)} -> {sorted(k)}"
                    for k, v in shapes.items())
        + "\n\n症状：只在某一条解析路径上出现的字段，前端读到 undefined "
          "而界面不报错。")
    (the_one,) = shapes
    return the_one


def test_every_construction_site_carries_the_keys_the_ui_reads():
    """契约守卫：界面读的那 6 个键，每一处都要有。"""
    missing = {}
    for name, ln, keys in BUILT:
        gap = sorted(UI_READ_KEYS - keys)
        if gap:
            missing[f"{name}:{ln}"] = gap
    assert not missing, (
        "这些构造点缺少界面要读的键："
        f"{missing}。\n症状：`VideoResult.vue` 读到 undefined，界面不报错——"
        "而这正是「按数据在哪张表枚举」会漏掉的那一半。")


def test_derived_site_only_overrides_existing_keys():
    """`{**base, ...}` 不得引入基类没有的键。

    它引入的键，基类那一支的平台就会缺——而基类那一支通常才是被测得最多的。
    """
    base_shapes = {keys for _, _, keys in BUILT}
    for name, ln, keys in DERIVED:
        for shape in base_shapes:
            novel = sorted(keys - shape)
            assert not novel, (
                f"{name}:{ln} 用 `{{**base}}` 引入了基类没有的键 {novel}，"
                f"而基类的键集是 {sorted(shape)}。"
                "派生点只该覆盖，不该新增。")


def test_no_private_looking_key_crosses_the_wire():
    """带下划线前缀的键不该出现在要发给前端的 format 里。

    这条是 `_direct_url` 的直接后果：它以前就长这样，靠一次人工审查删掉不算
    守卫——**下次有人再写一个 `_` 开头的键，必须有一条东西拦他**。
    私有约定靠前缀表达是廉价的，但前缀不会自己拦人。
    """
    for name, ln, keys in BUILT + DERIVED:
        private = sorted(k for k in keys if k.startswith("_"))
        assert not private, (
            f"{name}:{ln} 的 format 字典里有以下划线开头的键 {private}。"
            "下划线是在说「这是内部字段」，而这个字典是要整个发给前端的 —— "
            "约定靠前缀表达是廉价的，但前缀不会自己拦人。")


def test_ui_read_keys_are_written_down_here_not_derived():
    """反查表：断言里那份手写清单不是空的、也不是从构造点扫出来的。

    防的是「有人图省事，把 UI_READ_KEYS 改成从 BUILT 派生」——
    那样「构造点包含 UI_READ_KEYS」永远成立，一条也拦不住。
    """
    assert UI_READ_KEYS == frozenset({
        "format_id", "kind", "label", "resolution", "abr", "ext",
    }), (
        "UI_READ_KEYS 变了。它应当与 frontend/src/components/VideoResult.vue "
        "实际读的键逐字相等；若界面改了读法，同步改这里并说明为什么")
    assert "format_id" in UI_READ_KEYS, "至少要有 emit('download') 用的那个"
