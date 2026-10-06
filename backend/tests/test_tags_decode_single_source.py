r"""tags 解码的**结构守卫**：一份实现、一个真值（工单 #32）。

背景
----
`database.py` 的 tags 列 JSON 文本解码曾经长出四份逐字重复的副本
（`_decode_tags_text` 之外还有 `_project_video` / `_admin_community_item` /
`get_video_by_url` 各内联一份）。它们当时**语义完全一致**，所以四份实现可以
悄悄分叉，而没有任何测试会红：将来给真值加一条「去空白」「截断到 N 个」的规则
只改到一处，三个出口的 tags 就长得不一样——没有任何一处会告诉你是漏改的。

这个文件守的就是「不许再长出第四份」。

判据
----
`database.py` 里 `json.loads` 的调用点，其所属函数集合必须恰好等于
`REGISTERED_JSON_LOADS_OWNERS`。**用 AST 判，不用正则。**

为什么不用正则（这是本仓踩过的坑，不是假想）
------------------------------------------
天真的写法是 `json\.loads\([^)]*tags`：

    json.loads(item.get("tags") or "[]")
           ^^^^^^^^^^^^^^^ `item.get(` 的右括号让 [^)]* 提前截断

这一串在真实文件里是**匹配得到的**（`[^)]*` 吃到 `item.get(` 就停，后面还剩
`tags) or "[]")`，正则照样命中）——但它只对**恰好一种写法**有效。换成
`json.loads(row["tags"])`、`json.loads(*args)` 或跨行的参数，`[^)]*` 就失效，
于是**内联一份新副本却全绿**。守卫最坏的失败模式不是误报，是漏报。

AST 的写法不碰括号配对：`func.value.id == "json" and func.attr == "loads"`
认的是**调用本身**，与参数怎么写、跨不跨行、有没有嵌套括号都无关。

关于「只允许出现在 _decode_tags_text 内」
----------------------------------------
工单原文这句判据在当前树上**不成立**，本文件按实测修正后的版本实现。`database.py`
里共 6 处 `json.loads`，其中 2 处解的是**别的列**，与 tags 无关：

  - `_legacy_chat_history`        → parse_history.chat_history（老列只读）
  - `get_parse_history_detail`    → parse_history.video_data / subtitle_data

把这两处算成「违规」，等于要求真值同时去解码别的列——那是把收口做错。
所以守卫按「登记制」实现：**任何新增的 `json.loads` 都会红，并指名那个函数名**，
要新增就先在 `REGISTERED_JSON_LOADS_OWNERS` 里登记、写下它解的是哪一列。
本轮真正要防的分叉（三个 tags 出口重新内联）由 `TAGS_DECODE_EXITS` 那组断言
直接钉住，见 `test_tags_exits_have_no_inline_json_decode`。
"""
import ast
from pathlib import Path

DATABASE_PY = Path(__file__).resolve().parent.parent / "database.py"

#: 允许出现 `json.loads` 的函数。**每加一个名字都要写清它解的是哪一列**——
#: 登记制的全部价值就在于「新增解码点必须是一次显式决定」，而不是顺手写个 loads。
REGISTERED_JSON_LOADS_OWNERS = frozenset({
    # videos.tags —— tags 唯一真值，tags 解码只许在这里发生
    "_decode_tags_text",
    # parse_history.chat_history —— 工单 #8 之前唯一的存放处，老列只读
    "_legacy_chat_history",
    # parse_history.video_data / subtitle_data —— 与 tags 无关的两列
    "get_parse_history_detail",
})

#: 三个 tags 出口。它们**必须**调 _decode_tags_text，不得自带 json.loads。
TAGS_DECODE_EXITS = (
    "_project_video",
    "_admin_community_item",
    "get_video_by_url",
)


def find_json_loads_owners(source: str) -> dict:
    """返回 {所属函数名: [行号, ...]}，值为空表示没有任何 json.loads 调用。

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

        def visit_Call(self, node):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "loads"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            ):
                owner = stack[-1] if stack else "<module>"
                owners.setdefault(owner, []).append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return owners


def read_database_source() -> str:
    """保行尾读。database.py 是 CRLF/LF 混合工作区，本文件不做任何行尾相关断言。"""
    with open(DATABASE_PY, encoding="utf-8", newline="") as f:
        return f.read()


class TestJsonLoadsStaysRegistered:
    """`json.loads` 的调用点集合必须与登记表**完全相等**。

    用相等而不是子集：相等能同时抓住两个方向——新增（多了），
    以及真值被改名（少了）。
    """

    def test_owner_set_matches_registration_exactly(self):
        owners = find_json_loads_owners(read_database_source())
        actual = frozenset(owners)
        expected = REGISTERED_JSON_LOADS_OWNERS

        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        assert not unexpected and not missing, (
            "database.py 的 json.loads 调用点与登记表不符。\n"
            f"  登记表 REG: {sorted(expected)}\n"
            f"  实际   实际: {sorted(actual)}\n"
            + (f"  多出来（新增解码点，**未登记**）: {unexpected}\n" if unexpected else "")
            + (f"  登记表里已无对应函数: {missing}\n" if missing else "")
            + "\n修法：tags 解码一律调 _decode_tags_text；确需解别的列，\n"
            "     先在 REGISTERED_JSON_LOADS_OWNERS 里登记它解的是哪一列。"
        )

    def test_failure_message_names_the_offending_function(self):
        """失败消息必须指名那个函数名，而不是泛泛说「不该出现」。

        没有这条，将来新增一处 json.loads 的人看到的是一句「不该出现」，
        还得自己回去找是哪一行——守卫给出的信息量决定了它有没有用。
        """
        synthetic = (
            "def _decode_tags_text(raw):\n"
            "    return json.loads(raw or '[]')\n"
            "\n"
            "def some_new_helper(row):\n"
            "    return json.loads(row['whatever'])\n"
        )
        owners = frozenset(find_json_loads_owners(synthetic))
        unexpected = sorted(owners - REGISTERED_JSON_LOADS_OWNERS)
        assert unexpected == ["some_new_helper"], (
            f"合成用例没能认出未登记的解码点：{sorted(owners)}"
        )


class TestTagsExitsHaveNoInlineDecode:
    """三个 tags 出口不得自带 `json.loads` —— 本工单的直接回归守卫。

    比上面那组更尖：它不依赖登记表有没有更新。哪怕有人顺手把
    `some_new_helper` 登记进去，这三个出口重新内联仍然会红。
    """

    def test_tags_exits_have_no_inline_json_decode(self):
        owners = find_json_loads_owners(read_database_source())
        offenders = {
            name: lines for name, lines in owners.items()
            if name in TAGS_DECODE_EXITS
        }
        assert not offenders, (
            "这些 tags 出口里又长出了内联的 json.loads，tags 解码不再唯一：\n"
            + "\n".join(
                f"  {name}() 第 {lines[0]} 行起又自己解了一遍"
                for name, lines in sorted(offenders.items())
            )
            + "\n\n修法：删掉内联那几行，改成 item['tags'] = _decode_tags_text(item.get('tags'))。"
        )

    def test_each_tags_exit_delegates_to_the_truth(self):
        """三个出口的源码里必须**调用** _decode_tags_text。

        只钉「不许内联」不够：内联删干净了、却忘了接上真值，
        tags 会静默变成原始字符串（前端直接渲染成 `["编程"]` 那样带引号的怪东西）。
        """
        tree = ast.parse(read_database_source())
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_decode_tags_text":
                callers.add(node.lineno)

        assert callers, (
            "database.py 里没有任何地方调用 _decode_tags_text —— "
            "tags 真值已成死代码，说明收口做了一半。"
        )

        # 每个出口函数体内都应至少有一个调用点
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        missing = []
        for fname in TAGS_DECODE_EXITS:
            fn = next((f for f in funcs if f.name == fname), None)
            assert fn is not None, f"{fname} 不在 database.py 里了，守卫需要更新"
            body_calls = [c for c in callers if fn.lineno < c <= (fn.end_lineno or fn.lineno)]
            if not body_calls:
                missing.append(fname)
        assert not missing, (
            f"这些 tags 出口没有调用 _decode_tags_text：{missing}\n"
            "修法：item['tags'] = _decode_tags_text(item.get('tags'))"
        )


class TestGuardItselfDetectsViolations:
    """守卫的正例：给它违规代码，它必须报出来。

    没有这一组的话，把 find_json_loads_owners 改成恒返回 {} 就能让上面
    全部转绿，而全量测试照样通过——「守卫失效」与「守卫通过」将无法区分。
    形态照抄 check_db_fixture 的那道同类门禁（test_db_fixture_guard.py）。
    """

    def test_detects_inline_copy_in_a_tags_exit(self):
        synthetic = (
            "def _decode_tags_text(raw):\n"
            "    return json.loads(raw or '[]')\n"
            "\n"
            "def _project_video(row, fields):\n"
            "    item = dict(row)\n"
            "    try:\n"
            "        parsed = json.loads(item.get('tags') or '[]')\n"
            "    except (ValueError, TypeError):\n"
            "        parsed = []\n"
            "    item['tags'] = parsed\n"
            "    return item\n"
        )
        owners = find_json_loads_owners(synthetic)
        assert "_project_video" in owners, f"没认出内联副本：{owners}"
        assert owners["_project_video"] == [7], f"行号算错了：{owners}"

    def test_detects_rename_of_the_truth(self):
        """把真值改名，登记表的「相等」判定必须失效。

        这条证明守卫跟的是**函数归属**，不是拿名字当白名单糊过去。
        """
        synthetic = (
            "def _decode_tags_text_v2(raw):\n"
            "    return json.loads(raw or '[]')\n"
        )
        actual = frozenset(find_json_loads_owners(synthetic))
        assert actual != REGISTERED_JSON_LOADS_OWNERS, (
            "真值被改名后守卫仍然通过——它认的是名字白名单，不是归属"
        )
        assert sorted(actual - REGISTERED_JSON_LOADS_OWNERS) == ["_decode_tags_text_v2"]

    def test_detects_module_level_json_loads(self):
        """模块顶层（无所属函数）的 json.loads 也要算出来，不能被当成「无处可归」而放过。"""
        synthetic = "import json\nTAGS = json.loads('[]')\n"
        owners = find_json_loads_owners(synthetic)
        assert owners.get("<module>") == [2], f"顶层 json.loads 没被认出来：{owners}"

    def test_ignores_other_json_methods(self):
        """只认 json.loads。json.dumps / 别的模块的 loads 不该被算进来。"""
        synthetic = (
            "import json\n"
            "def f(x):\n"
            "    a = json.dumps(x)\n"
            "    b = pickle.loads(x)\n"
            "    return a, b\n"
        )
        assert find_json_loads_owners(synthetic) == {}, (
            "把 dumps / 别的模块的 loads 也算进来了"
        )