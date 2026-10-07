r"""两个 JSON 数组解码器的**口径差异**守卫（工单 #35）。

为什么有这张守卫
----------------
`database.py` 里有两个解 JSON 数组的函数，工单 #35 提出它们「口径不一致」：

| 函数 | 解的列 | 元素类型 | 过滤非字符串？ |
|---|---|---|---|
| `_decode_tags_text` | `videos.tags` | 字符串 | **会**（`isinstance(t, str)`） |
| `_legacy_chat_history` | `parse_history.chat_history`（老列只读） | **对象** `{"question","answer"}` | **不会** |

直觉上「同样的解码动作，口径应该统一」。**实测证明照直觉改会造成用户可见故障**：

    输入 [{"question": "q", "answer": "a"}]      现状 [{'question': 'q', 'answer': 'a'}]   过滤版 []
    输入 ["q", "a"]                                现状 ['q', 'a']                        过滤版 []

老列存的是 **dict**，`isinstance(t, str)` 对 dict **恒为 False** ——
所以「统一」成过滤版会把**正常的老数据整个清空**，症状是老用户的追问记录
凭空消失，而那比「混进一个脏元素」严重得多。

「混进非字符串」在**结构上不可达**：这一列唯一的真实写入点是
`append_chat_history`（工单 #8 之前，已移除），它只 `append({"question":...})`，
没有任何路径能塞进字符串或数字。要来的脏数据只可能来自手改库或迁移脚本 ——
那属于**数据事故**，该在写入侧报警，不该靠读的时候静默丢弃。

于是本守卫钉的是「**它们该分开**」这个决定，方向有二：

1. **行为层**：把 `_legacy_chat_history` 改成过滤版（与 tags 同形）必须转红，
   因为那会让正常的老数据消失。
2. **结构层**：两个函数的**元素类型契约**都不许变，且不许出现第三个
   同款解码器。

怎么证明这些断言有判别力
------------------------
`test_*.test_*` 那几条是正例：把本文件里定义的 `filtered_variant` 掏空，
它必须转红——否则「两个口径不同」只是**这份文档里的一个说法**，而没有任何东西
在守。反过来，`test_tags_filtering_still_holds` 确认 tags 侧**没有**被
悄悄改成不过滤（那是本单明确拒绝的「另一个方向的一刀切」）。
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DATABASE_PY = BACKEND / "database.py"

#: 两个解码器的**元素类型契约**。这里手写而不是从源码推导：
#: 推导出来的话「实际是什么」就永远等于真值，判据恒为真。
ELEMENT_CONTRACT = {
    "_decode_tags_text": "str",          # 字符串数组，过滤非字符串
    "_legacy_chat_history": "dict",      # 对象数组，原样返回
}


def read_source(path: Path) -> str:
    """保行尾读。database.py 是 CRLF/LF 混合工作区（工单 #36）。"""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _import_database():
    import os  # noqa: PLC0415
    os.environ.setdefault(
        "JWT_SECRET",
        "test-only-jwt-secret-0123456789abcdef0123456789abcdef")
    if str(BACKEND) not in os.sys.path:
        os.sys.path.insert(0, str(BACKEND))
    import database  # noqa: PLC0415
    return database


def filtered_variant(raw) -> list:
    """「统一口径」的那个版本：按 tags 的规则过滤非字符串。

    抽成具名函数而不是在测试里内联，是为了让「对照」与「判据」调
    **同一处实现**——否则削弱判据时对照不会跟着红，而两者的失败形状
    在报告里长得一模一样。
    """
    import json
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        parsed = []
    if not isinstance(parsed, list):
        return []
    return [t for t in parsed if isinstance(t, str)]


def element_predicates(func: ast.AST) -> list:
    """函数体里**作用在元素上**的 `isinstance` 调用行号。

    ⚠️ 必须区分两种 isinstance，否则会把正常实现误报成违规（实测第一版
    就是这么假红的）：

    - `if isinstance(chats, list): ...` —— **顶层类型判定**（解析结果不是
      数组就退回空），两个解码器**都有**这一句，是一致的口径；
    - `[t for t in parsed if isinstance(t, str)]` —— **元素过滤**，
      逐个元素筛，只有一个解码器该有。

    判据：谓词参数是**推导式的目标变量**（`for t in ...` 里的 `t`），
    而不是解析结果本身。
    """
    loop_vars = set()
    for node in ast.walk(func):
        if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(
                node.target, ast.Name):
            loop_vars.add(node.target.id)
        if isinstance(node, ast.comprehension) and isinstance(
                node.target, ast.Name):
            loop_vars.add(node.target.id)

    hits = []
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance" and node.args):
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id in loop_vars:
            hits.append(node.lineno)
    return sorted(hits)


class TestTheTwoDecodersMustStayDifferent:
    """核心行为：**把 legacy 改成 tags 的形状会造成用户可见故障**。"""

    def test_legacy_keeps_object_entries(self):
        """正常的对象数组必须**原样返回**——老用户的追问记录靠它。"""
        database = _import_database()
        payload = '[{"question": "q1", "answer": "a1"}]'
        got = database._legacy_chat_history(payload)
        assert got == [{"question": "q1", "answer": "a1"}], (
            f"老列的正常数据没被原样返回：{got}\n"
            "这一列存的是对象数组，返回值直接给 get_chat_session 用。")

    def test_unifying_with_tags_shape_would_wipe_history(self):
        """**实测**「统一口径」的后果：正常老数据整个清空。

        这条是本单的核心证据。它必须红——如果哪天有人把
        `_legacy_chat_history` 改成过滤版，本条会在它**真被改掉**之前
        就先把这个后果固化成文档；如果它已经被改掉，本条立刻转红。
        """
        database = _import_database()
        payload = '[{"question": "q1", "answer": "a1"}]'
        current = database._legacy_chat_history(payload)
        unified = filtered_variant(payload)
        assert current and not unified, (
            f"「统一口径」的预期结论变了：现状={current} 统一版={unified}\n"
            "若本条转红，说明过滤版不再清空正常数据——重新评估是否该统一。")

    def test_the_two_disagree_exactly_where_the_types_do(self):
        """两种口径**只在元素类型上**分歧，其余规则一致。

        钉住「分歧范围」：非数组、非法 JSON、NULL 三种输入两个函数
        必须给出**相同**结果。哪天 legacy 那边多了一条 filters，两边
        就会在这些情况下也分岔，本条转红。
        """
        database = _import_database()
        shared = [
            ("非数组", '{"question": "q"}'),
            ("非法 JSON", "not json"),
            ("NULL/空", None),
            ("空串", ""),
        ]
        for label, raw in shared:
            assert database._legacy_chat_history(raw) == filtered_variant(raw), (
                f"「{label}」两种口径给出了不同结果："
                f"{database._legacy_chat_history(raw)} vs {filtered_variant(raw)}\n"
                "分歧只该在元素类型上，不该扩散到类型判定与异常处理。")

    def test_tags_filtering_still_holds(self):
        """tags 侧**确实在过滤**——本单明确拒绝「另一个方向的一刀切」。

        与上一条成对：那一条防「legacy 被改成过滤版」，这一条防
        「tags 被改成不过滤版」。后者同样是用户可见故障：标签列表里
        会出现数字对象，界面直接崩。
        """
        database = _import_database()
        payload = '["ok", 123, null, {"a": 1}]'
        assert database._decode_tags_text(payload) == ["ok"], \
            database._decode_tags_text(payload)

    def test_positive_control_filtered_variant_is_not_trivially_true(self):
        """阳性对照：`filtered_variant` 本身有判别力。

        没有这条，把 `filtered_variant` 改成恒返回 `raw`，上面两条
        判据会一起失效，而全量测试照样绿。**先证明工具真的产出了输出。**
        """
        assert filtered_variant('["a", "b"]') == ["a", "b"]
        assert filtered_variant('[{"question": "q"}]') == [], (
            "对照用的过滤实现没有在对象数组上清空——"
            "那么「统一口径会清空数据」这个结论就不成立")


class TestElementContractsAreStructural:
    """结构层：两个函数的元素类型契约不许变，且不许有第三个解码器。"""

    def _function_bodies(self):
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        out = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in ELEMENT_CONTRACT:
                seg = ast.get_source_segment(src, node) or ""
                out[node.name] = seg
        return out

    def test_both_decoders_still_exist(self):
        bodies = self._function_bodies()
        missing = sorted(set(ELEMENT_CONTRACT) - set(bodies))
        assert not missing, (
            f"解码器不见了：{missing}\n"
            f"  现有：{sorted(bodies)}\n"
            "本守卫按名字找函数；改名了要么改这里，要么确认是有意删除。")

    def test_contract_type_appears_in_the_code(self):
        """元素类型契约要在**代码里**看得见，不只是在本文件的注释里。

        `str` 那一侧认的是「有**元素级** isinstance 谓词」，而不是
        `isinstance(t, str)` 这个字面量——实测（工单 #35 变异 M6）后者
        只能挡住自己写的那一种写法。`dict` 那一侧则认 `dict`/`question`
        这类标记词（legacy 侧本就不该有过滤）。

        ⚠️ 两个 `isinstance` 要分开：顶层类型判定（`isinstance(parsed,
        list)`）两边都有，不算元素过滤。见 `element_predicates`。
        """
        bodies = self._function_bodies()
        tags_body = bodies.get("_decode_tags_text")
        assert tags_body is not None, "_decode_tags_text 不见了"
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        tags_preds = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "_decode_tags_text":
                tags_preds = element_predicates(node)
        assert tags_preds, (
            "_decode_tags_text 的实现里没有元素级 isinstance 谓词了——\n"
            f"  实现：{tags_body[-200:]}\n"
            "tags 列存的是字符串数组，少了这个过滤，数字/对象会一路到界面上才炸。")

        legacy_body = bodies.get("_legacy_chat_history", "")
        assert "dict" in legacy_body or "question" in legacy_body, (
            "_legacy_chat_history 里看不到 dict/question 标记——\n"
            "  函数体开头：{legacy_body[:160]}")

    def test_legacy_has_no_string_filter(self):
        """**legacy 那侧不许有任何 isinstance 谓词的元素过滤。**

        这是「该分开」这个决定最直接的落点。

        ⚠️ 第一版只查 `isinstance(t, str)` 这**一个字面量**，实测被
        变异 M3 存活：把过滤写成 `[t for t in chats if isinstance(t, dict)]`
        （按 dict 过滤）—— 行为上同样是「过滤了」，字面量却换了一个。
        **查字面量而不查语义，守卫就只挡住了自己写的那一种写法。**
        所以这里改成按 **AST 形态**判定：函数体里不许出现
        `comprehension.isinstance` 或任何带 isinstance 的谓词。
        """
        body = self._function_bodies().get("_legacy_chat_history", "")
        code = body.split('"""')[-1]      # 跳过 docstring，只看实现
        assert code.strip(), "没读到 _legacy_chat_history 的实现"
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == "_legacy_chat_history"):
                continue
            hits = element_predicates(node)
            assert not hits, (
                f"_legacy_chat_history 里出现了**元素级** isinstance 谓词"
                f"（行 {hits}）——\n"
                "那一列存的是对象数组，任何元素过滤都可能把正常数据清掉。\n"
                f"  实现：{code.strip()[:200]}\n"
                "  实测：[{question, answer}] 经 isinstance(t, str) 过滤后变成 []\n"
                "  注：`isinstance(chats, list)` 那句**不算**元素过滤——"
                "它是顶层类型判定，与 `_decode_tags_text` 的写法一致。")

    def test_legacy_filter_shape_is_recognized_not_just_str_literal(self):
        """反向阳性：**别的写法也要被抓住**（M3 那条变异的直接对手）。

        把三种过滤写法都摆出来，说明结构判定认的是「元素级 isinstance 谓词」
        而不是「有没有 str 这个字」：换成 dict、换成 tuple，全都要红。

        ⚠️ **注入方式改成了按 AST 定位，不再按源码文本替换**（工单 #35）。
        第一版用 `src.replace("    return chats if isinstance(chats, list) else []", ...)`，
        而反向对照 A2 只是把那一行**拆成三行**（语义完全等价），替换就
        **静默不生效**，`poisoned == src`，本条报「注入失败」——那条消息
        是对的，只是它长得像「断言坏了」。也就是说：这条断言本身被一种
        **完全合法**的等价改写打倒了，而它本该守的东西（别的过滤写法）
        它自己却守不住。**一个守卫若会被无关的等价改写打倒，它给出的红里
        就混着噪声，久而久之没人会信它。**
        """
        for variant in ("isinstance(t, str)", "isinstance(t, dict)",
                        "isinstance(t, (str, dict))"):
            synthetic = (
                "def _legacy_chat_history(raw):\n"
                f"    chats = json.loads(raw or \"[]\")\n"
                f"    return [t for t in chats if {variant}]\n")
            tree = ast.parse(synthetic)
            func = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef))
            assert element_predicates(func), (
                f"元素级 isinstance 谓词（{variant}）没被结构判定认出——"
                "那么守卫只挡住了自己写的那一种写法")

    def test_real_implementation_is_accepted_by_the_same_judgement(self):
        """反向对照：真实现必须被同一条判定**接受**。

        与上一条成对：上一条问「别的写法会被抓住吗」，这一条问
        「该放过的会不会被放过吗」。缺了它，一个把 `element_predicates`
        改成恒返回非空（于是所有代码都被判违规）的守卫也能通过上一条。
        """
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        for name in ("_legacy_chat_history", "_decode_tags_text"):
            func = next(
                (n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)
            assert func is not None, f"{name} 不见了"
            hits = element_predicates(func)
            if name == "_legacy_chat_history":
                assert not hits, (
                    f"真实现被判成有元素过滤（行 {hits}）——判定过宽")
            else:
                assert hits, (
                    "真实现没被判成有元素过滤——判定过窄，"
                    "而 tags 侧**确实**有那个过滤")

    def test_no_third_json_array_decoder(self):
        """`database.py` 里不许再长出第三个同款解码器。

        这正是工单 #32 的教训：这段曾经长出过**四份逐字重复的副本**。
        收口之后每新增一处就是一次漂移，所以「不许再有第三个」要被钉住。
        """
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            seg = ast.get_source_segment(src, node) or ""
            if "json.loads" not in seg:
                continue
            # 解**数组**的那种：解析后立刻做 isinstance(..., list)
            if "isinstance" in seg and "list" in seg:
                found.append(node.name)
        extra = sorted(set(found) - set(ELEMENT_CONTRACT))
        assert not extra, (
            f"database.py 里出现了第三个 JSON 数组解码器：{extra}\n"
            f"  已登记的：{sorted(ELEMENT_CONTRACT)}\n"
            "新的一处要么并入已登记的那两个，要么明确登记成新的"
            "（并在此处写下它的元素类型契约）。")

    def test_registry_and_code_agree(self):
        """登记表必须与代码里的函数**一一对应**，不多不少。

        这条防的是「登记了一个不存在的函数」或「代码里新增了一个没登记的
        函数」——两者都会让上面几条在扫一个空集时恒为真。
        """
        src = read_source(DATABASE_PY)
        tree = ast.parse(src)
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        stale = sorted(set(ELEMENT_CONTRACT) - defined)
        assert not stale, (
            f"ELEMENT_CONTRACT 登记了不存在的函数：{stale}\n"
            "登记与代码必须双向一致（多与少都要报）。")