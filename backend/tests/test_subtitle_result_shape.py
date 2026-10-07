r"""字幕提取结果形状的**结构守卫**：一个出处、一个键集（工单 #37）。

背景
----
`SubtitleExtractor.extract()` 返回一个 7 键 dict，构造点**曾经有 5 处**
（`extract` / `_extract_bilibili` 两处 / `_transcribe_audio` / `_empty_asr`）。
它们当时**键集恰好一致**，所以行为上没问题，也不会有任何测试变红。危险只在
将来：加第 8 个字段（例如 ASR 语言 / 置信度）要改那 5 处，**漏改任何一处都不
报错**——漏改的那个返回缺键的 dict，下游 `KeyError` 变成 500。

现在形状收在 `_subtitle_result` 一个工厂里，键集登记在 `SUBTITLE_RESULT_KEYS`。
本文件守两件事：

1. `summarizer.py` 里**只有 `_subtitle_result` 一个函数**可以写出这些键；
2. 那唯一一处写出的键集，必须与 `SUBTITLE_RESULT_KEYS` **完全相等**。

判据
----
用 **AST**，不认键名字符串、不认行数。两个方向都抓：

- **多了**：别的函数又内联了一份构造（哪怕只写 3 个键）→ 红；
- **少了 / 换名**：工厂自己少写一个键，或把它改名 → 红，且**指名缺哪个键**。

为什么不用正则（这是本仓踩过的坑，不是假想）
------------------------------------------
天真的写法是数一把键名字符串：`re.findall(r'"(has_subtitle|language|…)"', body)`
或者扫 `"asr_fail_reason"` 出现几次。前者把**读**（`result["has_subtitle"]`、
`subtitle_data.get("fail_reason", "")`）和**写**一起数进来——`api_summarize.py`
里那两处读会让计数虚高，于是真有人漏改一处时它照样绿；后者数的是**出现次数**，
而这个数对「改了一个键名」和「换了个写法」都不敏感。

更要命的是这两种正则都**不认归属**：把 `has_subtitle` 写进一个新 helper 照样命中，
而把工厂里的键**删掉一个**、改成从别处 `dict(...)` 拼，可能一个都不命中。
守卫最坏的失败模式不是误报，是漏报。

AST 认的是「**这个 dict 字面量是不是形状本身**」：`ast.Dict` + 键是字符串常量，
再按**最内层所属函数**归属，与键名怎么写、跨不跨行、有没有嵌套括号都无关。

关于「5 处构造」这个数字
----------------------
工单正文写的是 4 处（`:123` / `:258` / `:475` / `:560`），并把 `_empty_asr`
列为第 4 处。动手前用 AST 扫全树实测是 **5 处**——漏了 `_extract_bilibili`
里那份 `empty`（`:173`，B 站 API 整体失败的返回），它当时也是完整的 7 键。
按 4 处写守卫会把这份漏在判据外，所以本文件按实测的 5 处定案。
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
SUMMARIZER_PY = BACKEND / "summarizer.py"

#: 唯一允许写出形状的函数。改名会让下面两条一起红——那是**故意的**：
#: 守卫跟着的是「唯一出处」这个性质，不是这五个字符。
FACTORY = "_subtitle_result"

#: 形状的锚点键。用来在**读**与**写**之间做区分：
#: 读到 `result["has_subtitle"]` 是消费形状，读到 `data.get("has_subtitle")`
#: 是防御，都不算构造。只有 dict 字面量里真的**写出**这个键才算。
ANCHOR_KEY = "has_subtitle"


def read_summarizer_source() -> str:
    """保行尾读。summarizer.py 是 CRLF 文件，本文件不做任何行尾相关断言。"""
    with open(SUMMARIZER_PY, encoding="utf-8", newline="") as f:
        return f.read()


def find_shape_literals(source: str) -> dict:
    """返回 {所属函数名: [(行号, 键元组), ...]}，只收**写出锚点键的 dict 字面量**。

    嵌套函数按**最内层**归属（栈顶），与 Python 实际作用域一致。
    """
    tree = ast.parse(source)
    owners: dict = {}
    stack: list = []

    class _Visitor(ast.NodeVisitor):
        def _enter_func(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_FunctionDef = _enter_func
        visit_AsyncFunctionDef = _enter_func

        def visit_Dict(self, node):
            keys = tuple(
                k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            )
            if ANCHOR_KEY in keys:
                owner = stack[-1] if stack else "<module>"
                owners.setdefault(owner, []).append((node.lineno, keys))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return owners


def declared_keys(source: str) -> tuple:
    """从源码里取 `SUBTITLE_RESULT_KEYS = (...)` 的字面量。取不到就抛。

    故意**从源码取**而不是 `from summarizer import SUBTITLE_RESULT_KEYS`：
    模块导入成功不等于这份登记还与工厂一致，源码才是守卫要看的对象。
    """
    tree = ast.parse(source)
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "SUBTITLE_RESULT_KEYS"
                for t in node.targets
            )
        ):
            value = ast.literal_eval(node.value)
            return tuple(value)
    raise AssertionError(
        "summarizer.py 里找不到 SUBTITLE_RESULT_KEYS 的模块级赋值——\n"
        "要么它被删了，要么被挪进了函数里。\n"
        "修法：把它放回模块顶层，守卫从那里读键集。"
    )


class TestShapeIsDefinedOnce:
    """`summarizer.py` 里写出 `has_subtitle` 的 dict 字面量，所属函数必须唯一。"""

    def test_only_the_factory_constructs_the_shape(self):
        owners = find_shape_literals(read_summarizer_source())
        actual = frozenset(owners)
        expected = frozenset({FACTORY})

        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        assert not unexpected and not missing, (
            "字幕提取结果的形状不再只有一个出处。\n"
            f"  期望只有: {FACTORY}()\n"
            f"  实际构造点所属函数: {sorted(actual)}\n"
            + "".join(
                f"  未登记的构造点 {name}() 第 {lines[0][0]} 行"
                f"（键: {list(lines[0][1])}）\n"
                for name, lines in sorted(owners.items())
                if name != FACTORY
            )
            + (f"  {FACTORY}() 不再构造形状了（被改名或删掉）\n" if missing else "")
            + "\n修法：一律改回 " + FACTORY + "()；"
            "加字段只改它一处，不要在调用点内联 dict。"
        )

    def test_failure_message_names_the_offending_function(self):
        """失败消息必须指名那个函数名，而不是泛泛说「不该出现」。

        没有这条，将来重新内联的人看到的是一句「不该出现」，还得自己回去找
        是哪一行——守卫给出的信息量决定了它有没有用。
        """
        synthetic = (
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False}\n"
            "\n"
            "def some_new_helper(row):\n"
            "    return {\n"
            "        'has_subtitle': bool(row.get('t')),\n"
            "        'full_text': row.get('x') or '',\n"
            "    }\n"
        )
        owners = find_shape_literals(synthetic)
        assert sorted(owners) == ["_subtitle_result", "some_new_helper"], (
            f"合成用例没认出新 helper 的内联构造：{sorted(owners)}"
        )
        assert owners["some_new_helper"][0][0] == 5, (
            f"行号算错了：{owners['some_new_helper']}"
        )


class TestFactoryMatchesTheDeclaration:
    """工厂写出的键集必须与 `SUBTITLE_RESULT_KEYS` **完全相等**。

    这是「加第 8 个字段」那条路上真正的护栏：加了字段却忘了同步登记（或反过来
    先登记了却没在工厂里写出来），两边的差集就是答案。
    """

    def _check(self, source: str) -> tuple:
        owners = find_shape_literals(source)
        assert FACTORY in owners, f"{FACTORY}() 里没有写出 {ANCHOR_KEY} 的 dict"
        literals = owners[FACTORY]
        assert len(literals) == 1, (
            f"{FACTORY}() 里有 {len(literals)} 处构造形状的 dict 字面量"
            f"（行 {[ln for ln, _ in literals]}），形状仍然没有单一出处"
        )
        return literals[0][1]

    def test_factory_key_set_equals_declaration(self):
        source = read_summarizer_source()
        actual = set(self._check(source))
        expected = set(declared_keys(source))

        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        assert not missing and not unexpected, (
            f"{FACTORY}() 写出的键与 SUBTITLE_RESULT_KEYS 不一致。\n"
            f"  登记的 7 键: {sorted(expected)}\n"
            f"  实际写出的 : {sorted(actual)}\n"
            + (f"  **漏写的键**: {missing}\n" if missing else "")
            + (f"  **多写的键**: {unexpected}\n" if unexpected else "")
            + "\n修法：两边改成一致。两者是同一份形状的两处登记，\n"
            "     只改一边正是本工单要防的那件事。"
        )

    def test_every_declared_key_is_really_constructed(self):
        """逐键确认：不只比集合，还要确认工厂真的**写出**了每一个键。

        集合相等本身已足够，但逐键断言能让失败消息直接指到那个键名，
        不必让人自己拿两串去对。
        """
        source = read_summarizer_source()
        factory_keys = set(self._check(source))
        for key in declared_keys(source):
            assert key in factory_keys, f"{FACTORY}() 没有写出键 {key!r}"


class TestReturnedDictReallyHasAllKeys:
    """运行期确认：工厂调出来的 dict 键集与登记一致，且 segments 恒为新 list。

    源码形状对了不等于运行期形状对了——AST 看的是字面量，而 `_subtitle_result`
    的 `segments` 走了一句 `if ... else []`。可变默认值在这里是个真风险：
    若哪次改成 `segments=[]` 写进签名，所有空结果会共享同一个 list。
    """

    def test_empty_call_has_exactly_the_declared_keys(self):
        from summarizer import SUBTITLE_RESULT_KEYS, _empty_asr, _subtitle_result

        result = _subtitle_result()
        assert tuple(result.keys()) == tuple(SUBTITLE_RESULT_KEYS), (
            f"工厂调出来的键集与登记不符：{tuple(result.keys())}"
        )
        assert _empty_asr().keys() == set(SUBTITLE_RESULT_KEYS)

    def test_segments_is_a_fresh_list_each_call(self):
        from summarizer import _empty_asr, _subtitle_result

        first = _subtitle_result()
        second = _subtitle_result()
        assert first["segments"] == second["segments"] == []
        assert first["segments"] is not second["segments"], (
            "segments 成了共享的可变默认值——一个结果里 append 会污染另一个"
        )
        assert _empty_asr()["segments"] is not _empty_asr()["segments"]


class TestGuardItselfDetectsViolations:
    """守卫的正例：给它违规代码，它必须报出来。

    没有这一组的话，把 `find_shape_literals` 改成恒返回 {} 就能让上面
    全部转绿，而全量测试照样通过——「守卫失效」与「守卫通过」将无法区分。
    形态照抄 `test_tags_decode_single_source.py` 里那道同类门禁。
    """

    def test_detects_a_deleted_key(self):
        """工厂少写一个键 → 必须报出**缺的是哪个键**。"""
        synthetic = (
            "SUBTITLE_RESULT_KEYS = ('has_subtitle', 'language', 'full_text')\n"
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False, 'full_text': ''}\n"
        )
        actual = set(find_shape_literals(synthetic)[FACTORY][0][1])
        expected = set(declared_keys(synthetic))
        missing = sorted(expected - actual)
        assert missing == ["language"], f"没认出缺哪个键：{missing}"

    def test_detects_an_added_key(self):
        """工厂多写一个键 → 必须报出**多的是哪个键**。"""
        synthetic = (
            "SUBTITLE_RESULT_KEYS = ('has_subtitle', 'language')\n"
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False, 'language': '', 'confidence': 1.0}\n"
        )
        actual = set(find_shape_literals(synthetic)[FACTORY][0][1])
        expected = set(declared_keys(synthetic))
        assert sorted(actual - expected) == ["confidence"]

    def test_detects_a_renamed_key(self):
        """键改名：数量不变，但相等判定必须失效。

        这条证明守卫比的是**键集相等**，不是「键数对不对」——
        正是工单要求的「判据要能指出缺的是哪个键」。
        """
        synthetic = (
            "SUBTITLE_RESULT_KEYS = ('has_subtitle', 'fail_reason')\n"
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False, 'reason': ''}\n"
        )
        actual = set(find_shape_literals(synthetic)[FACTORY][0][1])
        expected = set(declared_keys(synthetic))
        assert len(actual) == len(expected) == 2, "合成用例本身写错了键数"
        assert sorted(expected - actual) == ["fail_reason"]
        assert sorted(actual - expected) == ["reason"]

    def test_ignores_a_reader_of_the_shape(self):
        """读方不算构造：`result["has_subtitle"]`、`.get('has_subtitle')` 都不该被算。

        这条同时证明 AST 认的是 dict 字面量，不是键名在源码里出现过。
        """
        synthetic = (
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False}\n"
            "\n"
            "def reader(result):\n"
            "    if not result['has_subtitle']:\n"
            "        return result.get('has_subtitle', False)\n"
            "    return {'has_subtitle': True}\n"
        )
        owners = find_shape_literals(synthetic)
        assert sorted(owners) == [FACTORY, "reader"], (
            f"把读方也算成构造点了：{sorted(owners)}"
        )
        assert owners["reader"][0][0] == 7

    def test_detects_module_level_construction(self):
        """模块顶层（无所属函数）的构造也要算出来，不能被当成「无处可归」而放过。"""
        synthetic = (
            "SUBTITLE_RESULT_KEYS = ('has_subtitle',)\n"
            "SHAPE = {'has_subtitle': False}\n"
            "def _subtitle_result(**kw):\n"
            "    return {'has_subtitle': False}\n"
        )
        assert find_shape_literals(synthetic).get("<module>") == [(2, ("has_subtitle",))], (
            "顶层构造没被认出来"
        )
