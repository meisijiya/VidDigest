r"""额度种类的**结构守卫**：一处枚举，其余派生（工单 #38）。

背景
----
「有哪几种额度」原本在三个地方各自枚举一遍，只有 `database.QUOTA_KINDS`
是权威的。工单正文列了三处硬编码，**动手前用 AST 扫全树实测是五处**
——漏掉的两处恰恰是当时最危险的两处：

  - `api_summarize.py:72`（匿名分支的 `return {...}`）：工单正文完全没提。
    它是 `Dict` 而不是 `For`/`Assign`，只扫 `For` 与 `Assign` 的扫描器看不见它
    （本文件第一版探针就漏了它，见下面「为什么不用正则 / 只扫两种节点」）。
  - `admin_api.py` 的 `QuotaUpdateRequest` 字段声明：pydantic 字段是
    `AnnAssign` 而非 `Assign`，同样扫不到，而它的漏改后果是**静默丢弃**。

工单正文还有一条判据在当前树上**不成立**：它称 `/api/quota` 少一个键会让
前端 `quota.js` 走 `fallbackQuota` 兜底、显示旧口径数字。实测不成立——
`describeQuota` 只认 `parse` 与 `chat` 两个键名，第三种额度**它压根不看**，
少一个键的输出与不少**逐字相同**（不是兜底，是安静地少显示一种额度）。
⚠️ 这条结论**不是本文件测出来的**：它发生在 JS 里，Python 侧观察不到。
前端那条路径由 `frontend/tests/quota.test.mjs` 的「第三种额度在前端的形状」
真跑（多出第三种时文案逐字不变 / 只缺一半不触发兜底）；本文件只断言**后端
契约**——槽位键集与真值相等。

现在收成一处：`QUOTA_KINDS` 提成公开常量，另外两个模块遍历它。

**收口后仍有两处改不了，本文件就是它们的守卫**
---------------------------------------------------------
它们含额度种类的**专有数据**，循环产不出来，所以收口收不掉：

  1. `api_summarize._QUOTA_LABELS`：额度种类 → 中文名。
  2. `admin_api.QuotaUpdateRequest` 的字段声明：pydantic 要求静态字段，
     且**未声明的键被静默丢弃**——漏登记的症状是「管理员传了也改不动，
     且返回 200」。

判据
----
1. `_QUOTA_LABELS` 的键集 == `QUOTA_KINDS` 的键集；
2. `QuotaUpdateRequest` 的字段集 == `{quota_field_name(k) for k in QUOTA_KINDS}`；
3. **结构扫描**：`backend/` 下（测试与 `.scratch` 除外）再不许有第三处
   枚举额度种类的字面量。这一条是核心——它让「加第三种额度」在两处之外
   彻底不需要改代码，也在有人重新硬写 `("parse", "chat")` 时立刻转红。

三条判据都在失败消息里**指名缺了哪几种**，不只是「集合不相等」。

为什么用 AST（这是本仓踩过的坑，不是假想）
------------------------------------------
天真的写法是搜文本：`re.findall(r'"(parse|chat)"', src)` 数出现次数。
它有三个各自独立的漏法：

  - **不认归属**：把 `parse` 写进一个新 helper、或者作为**值**而不是键
    （`kind == "parse"`）出现，照样命中，计数虚高；
  - **数次数而非集合**：把 `("parse", "chat")` 改成 `("chat", "parse")`
    计数不变，绿；改成三元组也不变；
  - **跨不过行尾**：`database.py` 是 CRLF/LF 混合工作区（工单 #36），
    多行 anchor 差一个 `\r` 就 0 次命中，而「0 次命中」看着像
    「文件里没这段代码」，方向天然指错。

AST 认的是**这个字面量是不是一份「种类集合」**：节点是
`Tuple/List/Set/Dict` 且元素/键是字符串常量，与它怎么排版、跨不跨行、
写在哪个文件里都无关。

只扫 `For` 与 `Assign` 也是不够的（第一版探针的实测漏法）
--------------------------------------------------------
`api_summarize.py:72` 是 `return {...}`、`QuotaUpdateRequest` 是
`AnnAssign`，两者都不是 `For`/`Assign`。所以本文件的扫描器
`find_kind_enumerations` **遍历每一个节点**，凡「容器字面量里出现了
parse/chat 这类额度名」都算，不限节点种类。守卫最坏的失败模式是漏报。

关于「三处硬编码」这个数字
--------------------------
工单正文写的是三处（`api_summarize._quota_payload` / `_QUOTA_LABELS` /
`admin_api` 循环）。动手前 AST 扫全树实测**五处**字面量枚举，漏了
`api_summarize.py:72` 与 pydantic 字段声明。所以判据按实测的五处定案，
且第 3 条是「全树不许再有第三处」——按工单原文字面写会漏掉上面那两处。
"""
import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
DATABASE_PY = BACKEND / "database.py"

#: 扫描范围：backend/ 下的生产代码。`tests/` 与 `.scratch/` 排除。
SCAN_SKIP_DIRS = {"venv", "__pycache__", ".pytest_cache", "node_modules", "tests",
                  ".scratch", "data"}


def read_source(path: Path) -> str:
    """保行尾读。database.py 是 CRLF/LF 混合工作区，本文件不做行尾断言。"""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def find_kind_enumerations(source: str) -> list:
    """返回 [Site, ...]，凡容器字面量里含额度名就算。

    `Site` = {lineno, names, node, func, target}：

      - `func`   最近一层所属函数名（模块顶层为 ``"<module>"``）
      - `target` 最近一层赋值的目标名（不在赋值里为 ``None``）

    带上 func/target 是为了让**豁免能锚到具体那一处**，而不是整个文件——
    「按文件名豁免」是本仓踩过的坑：豁免名单一旦按文件给，那一个文件里
    将来出现的所有新违规都被一起放过（实测变异 M2 / M4 就是这么活的）。

    **遍历每一个节点**，不限节点种类——`return {...}`（Dict）与 pydantic
    的 `AnnAssign` 都是漏过的地方，见文件头。

    认的是「容器字面量的元素/键里有额度名」，所以下面这些**都**能被抓到：
        for kind in ("parse", "chat"):
        _LABELS = {"parse": "解析"}
        return {"parse": None, "chat": None}
        x = ["parse"]
        d = {f"{kind}_limit": 1}          # f-string 键也记一笔
    而 `kind == "parse"`（比较）与注释里的 "parse" 都不会被误算。
    """
    tree = ast.parse(source)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node, kinds):
        """向上找最近的 kinds 之一的祖先节点。"""
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, kinds):
                return cur
            cur = parents.get(cur)
        return None

    def func_of(node):
        fn = enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        return fn.name if fn is not None else "<module>"

    def target_of(node):
        owner = enclosing(node, (ast.Assign, ast.AnnAssign))
        if owner is None:
            return None
        if isinstance(owner, ast.AnnAssign):
            return getattr(owner.target, "id", None)
        names = [t.id for t in owner.targets if isinstance(t, ast.Name)]
        return names[0] if names else None

    def names_of(node):
        out = set()
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            for el in node.elts:
                if isinstance(el, ast.Constant) and isinstance(el.value, str):
                    out.add(el.value)
                elif isinstance(el, ast.JoinedStr):
                    out.add(_fstring_literal_text(el))
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if key is None:
                    continue
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    out.add(key.value)
                elif isinstance(key, ast.JoinedStr):
                    out.add(_fstring_literal_text(key))
        return out

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Tuple | ast.List | ast.Set | ast.Dict):
            continue
        found = names_of(node)
        if found & KIND_NAMES:
            hits.append({
                "lineno": node.lineno,
                "names": sorted(found & KIND_NAMES),
                "node": type(node).__name__,
                "func": func_of(node),
                "target": target_of(node),
            })
    return hits


def is_labels_constant(site: dict) -> bool:
    """这一处是不是 `_QUOTA_LABELS` 那份**专有数据**（唯一允许的枚举）。

    锚点是「模块顶层的 `_QUOTA_LABELS` 赋值」这一处，不是文件名。
    按文件豁免会让该文件里将来出现的每一处新枚举都被放过——
    那样这条守卫在 `api_summarize.py` 上就等于不存在（实测变异 M2/M4）。
    """
    return site["func"] == "<module>" and site["target"] == LABELS_TARGET


def _fstring_literal_text(node: ast.JoinedStr) -> str:
    """把 f-string 里的字面量片段拼出来：`f"{kind}_limit"` → `_limit`。"""
    return "".join(v.value for v in node.values
                   if isinstance(v, ast.Constant) and isinstance(v.value, str))


# ── 真值：从生产代码导入，而不是在这里再抄一份 ──────────────────
import os  # noqa: E402

os.environ.setdefault(
    "JWT_SECRET", "test-only-jwt-secret-0123456789abcdef0123456789abcdef"
)
if str(BACKEND) not in os.sys.path:
    os.sys.path.insert(0, str(BACKEND))

import admin_api  # noqa: E402
import api_summarize  # noqa: E402
import database  # noqa: E402

KIND_NAMES = frozenset(database.QUOTA_KINDS)

#: `_QUOTA_LABELS` 是「专有数据」而非循环产物——它必须逐个与常量同步，
#: 所以豁免**它这一处赋值**。豁免锚在「模块顶层的这个赋值目标」上，
#: 不是文件名（按文件豁免 = 该文件将来所有新违规一起放过）。
LABELS_TARGET = "_QUOTA_LABELS"


def describe_missing(actual, expected) -> str:
    """把差集写成人能读的话：缺哪些、多哪些。"""
    miss = sorted(expected - actual)
    extra = sorted(actual - expected)
    msg = f"  真值(database.QUOTA_KINDS): {sorted(expected)}\n  实际:                {sorted(actual)}\n"
    if miss:
        msg += f"  ❌ 少登记: {miss}\n"
    if extra:
        msg += f"  ❌ 多出来（真值里没有的额度种类）: {extra}\n"
    return msg


class TestQuotaLabelsCoverEveryKind:
    """`_QUOTA_LABELS`（额度种类 → 中文名）的键集必须等于真值。

    这处收不掉：中文名是专有数据，循环产不出来。工单原文认为它漏了会
    `KeyError`（响亮），实测确实响亮（`_check_quota_permission` 直接下标取），
    但响亮不等于有守卫——`KeyError` 只在真有人调用那类额度时才会发生，
    「加了种类忘了登记、于是没人调用」这条路径全程无声。
    """

    def test_labels_keys_equal_quota_kinds(self):
        actual = frozenset(api_summarize._QUOTA_LABELS)
        expected = KIND_NAMES
        assert actual == expected, (
            "api_summarize._QUOTA_LABELS 与 database.QUOTA_KINDS 的键集不相等。\n"
            + describe_missing(actual, expected)
            + "\n修法：在 _QUOTA_LABELS 里补上缺的那种额度的中文名。\n"
            "     别删多出来的那种——那说明真值改了而中文名没跟上。"
        )

    def test_labels_only_hold_nonempty_strings(self):
        """中文名不许是空串或空白：空串在界面上与「没登记」表现相同。"""
        blank = sorted(k for k, v in api_summarize._QUOTA_LABELS.items()
                       if not isinstance(v, str) or not v.strip())
        assert not blank, f"这些额度的中文名是空的：{blank}"

    def test_missing_label_fails_loudly_at_call_time(self):
        """实测「漏登记」在**真调用时**是什么形状（工单正文称 KeyError）。

        这条不是守卫，是把工单那条判据**验成事实**：它响亮，但响亮的代价是
        500 而非 400，且只在有人调用那类额度时才响——所以它不能替代上面那条。
        """
        with pytest.raises(KeyError) as exc:
            api_summarize._check_quota_permission(None, "no_such_kind")
        assert "no_such_kind" in str(exc.value)


class TestQuotaUpdateRequestDeclaresEveryKind:
    """`QuotaUpdateRequest` 的字段集必须覆盖全部额度种类。

    **这是本工单最危险的一处，原因是 pydantic 静默丢弃未声明的键**：
    实测 `QuotaUpdateRequest(parse_limit=5, export_limit=7)` 之后
    `model_dump(exclude_unset=True)` 只剩 `parse_limit`——管理员传了
    `export_limit` 也改不动，而端点一路 200 返回「额度已更新」。
    """

    def test_model_fields_equal_naming_convention(self):
        expected = frozenset(
            admin_api.quota_field_name(kind) for kind in database.QUOTA_KINDS
        )
        actual = frozenset(admin_api.QuotaUpdateRequest.model_fields)
        assert actual == expected, (
            "admin_api.QuotaUpdateRequest 的字段集与额度种类不相等。\n"
            + describe_missing(actual, expected)
            + "\n修法：在 QuotaUpdateRequest 里补上缺的那个字段（声明成 `Any = None`）。\n"
            "     字段名由 quota_field_name 派生，不要手写。"
        )

    def test_undeclared_kind_is_silently_dropped(self, db, make_user):
        """**实测**漏声明的后果：静默丢弃 + 仍然 200 成功。

        这条钉住「为什么需要上面那条守卫」——不是怕代码难看，是怕这个形状。
        守的是「pydantic 的静默丢弃」，不是任何内部实现细节。
        """
        uid = make_user()
        payload = admin_api.QuotaUpdateRequest(**{
            "parse_limit": 5, "undeclared_limit": 7,
        })
        dumped = payload.model_dump(exclude_unset=True)
        assert "undeclared_limit" not in dumped, (
            "pydantic 不再静默丢弃未声明的键了——那么上面那条守卫的依据变了，"
            "本文件要重新评估（也许是该加 extra='forbid' 让它响亮地报错）"
        )
        assert dumped == {"parse_limit": 5}


class TestNoThirdEnumerationExists:
    """核心结构守卫：`backend/` 下不许再出现第三处枚举额度种类的字面量。

    这条让「加第三种额度」在 `_QUOTA_LABELS` 与 `QuotaUpdateRequest`
    之外**不需要改任何代码**，也在有人重新硬写 `("parse", "chat")` 时立刻转红。
    """

    def _scan(self):
        found = []
        for path in sorted(BACKEND.rglob("*.py")):
            rel_parts = path.relative_to(BACKEND).parts
            if any(part in SCAN_SKIP_DIRS for part in rel_parts):
                continue
            if path == DATABASE_PY:
                continue  # 真值自己，见 test_authority_constant_is_the_only_dict
            rel = path.relative_to(BACKEND).as_posix()
            for site in find_kind_enumerations(read_source(path)):
                if is_labels_constant(site):
                    continue
                found.append((rel, site))
        return found

    def test_only_labels_may_enumerate(self):
        offenders = self._scan()
        assert not offenders, (
            "额度种类在 QUOTA_KINDS 与 _QUOTA_LABELS 之外又被枚举了一遍"
            "（加第三种额度时这里会静默漏改）：\n"
            + "\n".join(
                f"  {rel}:{s['lineno']}  {s['node']}  {s['names']}"
                f"  （函数 {s['func']}）" for rel, s in offenders)
            + "\n\n修法：改成遍历 database.QUOTA_KINDS（值本身含专有数据、"
              "循环产不出来的除外，那就登记进上面那两条守卫）。"
        )

    def test_authority_constant_is_the_only_dict(self):
        """`database.QUOTA_KINDS` 自身必须是那份 dict 字面量，且键集自洽。

        防止有人「顺手」把它拆成两个 dict、或改成 comprehension——
        那会让上面所有判据的参照系悄悄换掉。
        """
        hits = find_kind_enumerations(read_source(DATABASE_PY))
        assert len(hits) == 1, (
            f"database.py 里出现了 {len(hits)} 处额度种类枚举，应恰好 1 处（真值）：\n"
            + "\n".join(f"  第 {h['lineno']} 行 {h['node']} {h['names']}" for h in hits)
        )
        assert hits[0]["node"] == "Dict", f"真值必须是 dict 字面量，实得 {hits[0]['node']}"
        assert hits[0]["target"] == "QUOTA_KINDS", (
            f"真值那个赋值的名字变了（实得 {hits[0]['target']!r}）——"
            "守卫的参照系换了，这条要重新评估"
        )

    def test_scanner_detects_a_hardcoded_loop(self):
        """守卫的正例：给它一份硬编码循环，它必须报出来。

        没有这条，把 `find_kind_enumerations` 改成恒返回 [] 就能让上面
        全部转绿，而全量测试照样通过。
        """
        synthetic = (
            "def admin_set_quota(kind):\n"
            "    for kind in ('parse', 'chat'):\n"
            "        pass\n"
        )
        hits = find_kind_enumerations(synthetic)
        assert hits and hits[0]["names"] == ["chat", "parse"], f"没认出硬编码循环：{hits}"
        assert hits[0]["lineno"] == 2, f"行号算错了：{hits}"
        assert hits[0]["func"] == "admin_set_quota", f"归属认错了：{hits}"

    def test_scanner_detects_the_return_literal_the_ticket_missed(self):
        """第一版探针漏掉的那处：`return {...}`，不是 For/Assign。

        这条钉住「遍历每一个节点」这个决定本身——退回只扫 For/Assign 时它会红。
        """
        synthetic = (
            "def get_quota(user):\n"
            "    if not user:\n"
            "        return {'logged_in': False, 'parse': None, 'chat': None}\n"
        )
        hits = find_kind_enumerations(synthetic)
        assert hits, "漏掉了 return {...} 字面量——扫描器退化成只扫 For/Assign 了？"
        assert hits[0]["lineno"] == 3, f"行号算错了：{hits}"
        assert hits[0]["func"] == "get_quota", f"归属认错了：{hits}"

    def test_exemption_is_scoped_to_the_labels_assignment(self):
        """**豁免必须锚在那一处赋值上，不能按文件名给**（工单 #38 实测）。

        变异 M2/M4 都发生在 `api_summarize.py`：把已经收口的循环重新硬写回去，
        守卫当时是**绿的**——因为豁免写成了「跳过整个 api_summarize.py」。
        本条把「同一个文件里、非 `_QUOTA_LABELS` 的枚举仍要被抓」钉死。
        """
        synthetic = (
            "from database import QUOTA_KINDS\n"
            "_QUOTA_LABELS = {'parse': 'a', 'chat': 'b'}\n"
            "\n"
            "def _quota_payload(user_id):\n"
            "    payload = {}\n"
            "    for kind in ('parse', 'chat'):\n"
            "        payload[kind] = 1\n"
            "    return payload\n"
        )
        hits = find_kind_enumerations(synthetic)
        labels_hits = [h for h in hits if is_labels_constant(h)]
        other_hits = [h for h in hits if not is_labels_constant(h)]
        assert len(labels_hits) == 1, f"没认出豁免的那处：{hits}"
        assert len(other_hits) == 1, (
            f"同文件里的硬编码循环被豁免一起放过了（{len(other_hits)} 处被抓）：{hits}\n"
            "豁免必须锚到 `_QUOTA_LABELS` 这一处赋值，不是整个文件"
        )
        assert other_hits[0]["func"] == "_quota_payload", f"归属认错了：{other_hits}"

    def test_exemption_requires_module_level(self):
        """豁免的**两个条件缺一不可**——尤其是「模块顶层」那半边。

        `is_labels_constant` 是 `func == "<module>" and target == "_QUOTA_LABELS"`。
        实测把 `func` 那一半删掉，全套 16 条**照样全绿**：真值树里
        `_QUOTA_LABELS` 只有一处、且恰在模块顶层，于是那半边条件当时
        是冗余的——**没有任何断言守着它**。而它恰好是防这个形状的：

            def reset_labels():
                _QUOTA_LABELS = {"parse": "a", "chat": "b", "export": "c"}

        只按名字豁免时，函数内那份（一份新的、要人手工维护的注册表）
        会被当成真标签表放过，而它永远不会被 `test_labels_keys_equal_quota_kinds`
        读到（那条只 import `api_summarize._QUOTA_LABELS`）。**豁免放过的枚举，
        正是那条键集判据看不见的那一处。**
        """
        synthetic = (
            "_QUOTA_LABELS = {'parse': 'a', 'chat': 'b'}\n"
            "\n"
            "def reset_labels():\n"
            "    _QUOTA_LABELS = {'parse': 'a', 'chat': 'b', 'export': 'c'}\n"
            "    return _QUOTA_LABELS\n"
        )
        hits = find_kind_enumerations(synthetic)
        exempt = [h for h in hits if is_labels_constant(h)]
        caught = [h for h in hits if not is_labels_constant(h)]
        assert len(exempt) == 1, f"豁免的不该超过模块顶层那一份：{hits}"
        assert exempt[0]["func"] == "<module>", f"豁免认错了归属：{exempt}"
        assert len(caught) == 1, (
            f"函数内的 _QUOTA_LABELS 被豁免放过了（{hits}）——"
            "豁免必须同时要求「模块顶层」，否则函数里那份手工维护的"
            "注册表会成为守卫看不见的死角"
        )
        assert caught[0]["func"] == "reset_labels", f"归属认错了：{caught}"

    def test_exemption_requires_the_right_target_name(self):
        """豁免的另一半：**模块顶层还不够，名字也得是 `_QUOTA_LABELS`**。

        与上一条对称：真值树上只有一处模块顶层 dict 含额度名，且它恰好
        就是 `_QUOTA_LABELS`，所以两条条件在**当前树上是等价的** ——
        实测只删掉名字那半边（`func == "<module>"`）全套照样全绿。

        它防的是将来这个反例：某个别的模块写了一份顶层注册表，
        变量名不是 `_QUOTA_LABELS`（比如 `EXTRA_LABELS`、`LEGACY_KINDS`）。
        那种枚举恰恰是最该被抓的——它不在 `test_labels_keys_equal_quota_kinds`
        的比对范围内（那条只 import `api_summarize._QUOTA_LABELS`），
        却是真真实实的一处手工枚举。**豁免放过的枚举，正是键集判据看不见的
        那一处。**
        """
        synthetic = (
            "_QUOTA_LABELS = {'parse': 'a', 'chat': 'b'}\n"
            "\n"
            "EXTRA_LABELS = {'parse': 'a', 'chat': 'b', 'export': 'c'}\n"
        )
        hits = find_kind_enumerations(synthetic)
        exempt = [h for h in hits if is_labels_constant(h)]
        caught = [h for h in hits if not is_labels_constant(h)]
        assert len(exempt) == 1, f"豁免的不该超过 `_QUOTA_LABELS` 那一处：{hits}"
        assert exempt[0]["target"] == LABELS_TARGET, f"豁免认错了目标：{exempt}"
        assert len(caught) == 1, (
            f"模块顶层但名字不对的枚举被豁免放过了（{hits}）——"
            "豁免必须同时要求 target 是 `_QUOTA_LABELS`，"
            "否则任何新的顶层注册表都会成为守卫看不见的死角"
        )
        assert caught[0]["target"] == "EXTRA_LABELS", f"目标认错了：{caught}"

    def test_scanner_ignores_a_comparison_against_one_kind(self):
        """`kind == "parse"` 是**用法**不是枚举，不该被算进来。

        少这一条，扫描器会因为误报而被人加豁免，豁免一多就等于没有守卫。
        """
        synthetic = (
            "def f(kind):\n"
            "    if kind == 'parse':\n"
            "        return 1\n"
            "    return 0\n"
        )
        assert find_kind_enumerations(synthetic) == [], (
            "把单个额度名的比较算成枚举了——会逼人加豁免，豁免一多守卫就废了"
        )

    def test_scanner_ignores_kind_names_inside_fstrings_and_calls(self):
        """`f"{kind}_limit"` 与 `check_quota_kind(u, "parse")` 都不是枚举。"""
        synthetic = (
            "def f(kind, u):\n"
            "    field = f'{kind}_limit'\n"
            "    return check_quota_kind(u, 'parse')\n"
        )
        assert find_kind_enumerations(synthetic) == [], (
            f"把用法算成枚举了：{find_kind_enumerations(synthetic)}"
        )


class TestPayloadCarriesEveryKind:
    """`/api/quota` 的响应必须为**每种**额度都带一个槽位键（工单 #38 核心行为）。

    这条不依赖任何 AST 判据，直接打真实函数——它守的是外部可观察的形状。
    """

    def test_anonymous_payload_has_a_slot_per_kind(self, db):
        import asyncio

        body = asyncio.run(api_summarize.get_quota(user=None))
        missing = sorted(k for k in database.QUOTA_KINDS if k not in body)
        assert not missing, (
            f"匿名 /api/quota 少了这些额度的槽位键: {missing}\n"
            f"  实际键: {sorted(body)}"
        )

    def test_logged_in_payload_has_a_slot_per_kind(self, db, make_user):
        import asyncio

        uid = make_user()
        body = asyncio.run(
            api_summarize.get_quota(user={"id": uid, "email": "a@example.com"})
        )
        missing = sorted(k for k in database.QUOTA_KINDS if k not in body)
        assert not missing, (
            f"登录 /api/quota 少了这些额度的槽位键: {missing}\n"
            f"  实际键: {sorted(body)}"
        )

    def test_admin_user_item_has_a_block_per_kind(self, db, make_user):
        """后台用户行也必须为每种额度给出 4 个派生字段。"""
        uid = make_user()
        row = database.admin_user_detail(uid)
        suffixes = ("_used", "_limit", "_limit_override", "_limit_source")
        missing = [f"{k}{s}" for k in database.QUOTA_KINDS for s in suffixes
                   if f"{k}{s}" not in row]
        assert not missing, f"后台用户行缺这些字段: {missing}"

    def test_missing_kind_in_payload_is_invisible_not_fallback(self, db, make_user):
        """工单正文那条判据不成立：**少一个键不会触发前端兜底**。

        工单称漏键会让 `quota.js` 走 `fallbackQuota`、显示旧口径数字。
        实测不成立：`describeQuota` 的降级条件是 `!parse && !chat`
        （**两个都缺**才降级，`frontend/src/lib/quota.js:44`），少一个键时它
        只是安静地少渲染一种额度，输出与不少**逐字相同**。

        ⚠️ **前端那条路径由 `frontend/tests/quota.test.mjs` 真跑，本文件不测它。**
        「fallback 与否」发生在 JS 里，Python 侧无法观察。所以这里断言的
        只能是**后端契约**——槽位键集与真值相等，而这正是让前端无从缺键的
        原因。别把本条当成「前端行为已验证」的证据。
        """
        import asyncio

        uid = make_user()
        body = asyncio.run(
            api_summarize.get_quota(user={"id": uid, "email": "a@example.com"})
        )
        assert set(database.QUOTA_KINDS) <= set(body), (
            f"槽位键集与真值不相等: {sorted(set(database.QUOTA_KINDS) - set(body))}"
        )
