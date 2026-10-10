"""门禁清单本身的门禁：`DB_FUNCS` 漏一个函数，那条路径就静默失明。

## 为什么需要这条测试

`check_db_fixture.py` 是一道 AST 门禁：找出「会触库却没取 `db` 夹具」的测试，
避免它们静默连上开发机上的真实 `backend/data/app.db`。

它的判据是一份**手工维护的名单** `DB_FUNCS`。那份名单是承重的 ——
本文件末尾的 `test_gate_actually_goes_blind_without_the_entry` 用变异证明：
把某个名字从名单里删掉，门禁立刻对那条路径失明。

但**承重不等于被守住**。实测（2026-10-03）：`DB_FUNCS` 这个符号在全仓只出现在
`check_db_fixture.py` **内部**，没有任何测试断言它的完整性。于是：

    新增一个会触库的函数 → 写了测试 → 忘了加进名单
        → 门禁对那条路径静默失明
        → **不会有任何东西变红**

这与本轮另一个已修缺陷同形（`stripComments` 把 `https://` 当行注释，
导致「前端无厂商地址」一直是假绿）：**一个东西存在 ≠ 它承重；它承重 ≠ 它被守住。**

## 这条测试怎么判定「谁会触库」

不手工列第二份名单（那只是把同一个坑挖得更深），而是**从源码算**：
解析 `database.py` / `model_catalog.py` 的 AST，建出「函数 -> 它调用了哪些
函数」，以 `get_db` 为根做**传递闭包**。闭包里的每个函数都是「会触库的」，
必须逐个在 `DB_FUNCS` 里。

必须算闭包而不是只看直接调用：`check_quota` 自己不调 `get_db`，
它调 `check_quota_kind`，后者才调 `get_db`。只看直接调用会把
`check_quota` 漏掉，而它就在名单里且极易被误以为「有它就够了」。

## 边界：为什么 `get_db_path` 不算触库

`get_db_path()` 只做 `os.makedirs` 并返回路径字符串，不建连接。
把它算成触库会让这道测试逼人往名单里塞一个不需要夹具的名字，
而名单一旦塞了不该塞的东西，第二个守门人就懒得看了。
"""

import ast
from pathlib import Path

import check_db_fixture

BACKEND = Path(__file__).resolve().parent.parent

#: 触库的根。名单里必须出现的函数 = 这些函数的传递闭包。
ROOTS = {"get_db"}

#: 参与扫描的模块。它们的函数构成「会触库」的候选集。
SCANNED_MODULES = ["database.py", "model_catalog.py"]


def _module_ast(name: str) -> ast.Module:
    return ast.parse((BACKEND / name).read_text(encoding="utf-8"))


def _function_call_map(name: str) -> dict[str, set[str]]:
    """模块里每个顶层函数 -> 它体内直接调用到的模块内函数名。"""
    tree = _module_ast(name)
    calls: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        found: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called:
                    found.add(called)
        calls[node.name] = found
    return calls


def _db_touching_functions() -> set[str]:
    """从源码算出所有「（传递地）会触库」的函数名。"""
    calls: dict[str, set[str]] = {}
    for module in SCANNED_MODULES:
        calls.update(_function_call_map(module))

    touching: set[str] = set(ROOTS)
    # 反复扫一遍直到不动点：闭包深度不由调用顺序决定
    changed = True
    while changed:
        changed = False
        for func, callees in calls.items():
            if func in touching:
                continue
            if callees & touching:
                touching.add(func)
                changed = True
    return touching


def test_every_db_touching_function_is_in_the_gate_list():
    """每个会触库的函数都必须在 DB_FUNCS 里，否则门禁对它失明。"""
    touching = _db_touching_functions()
    missing = sorted(touching - check_db_fixture.DB_FUNCS)
    assert not missing, (
        f"这些函数会（传递地）触库，但不在 check_db_fixture.DB_FUNCS 里：\n"
        f"    {missing}\n"
        "  后果：测试调它们却不取 db 夹具时，门禁**不会**报，"
        "于是那条测试会静默连上开发机上的真实 app.db。\n"
        "  修法：把上面每个名字加进 DB_FUNCS，并写清它属于哪一组。"
    )


def test_the_scan_finds_the_roots_otherwise_it_is_vacuous():
    """反向自检：算法本身得先能看见根，否则上面那条恒真。"""
    touching = _db_touching_functions()
    assert ROOTS <= touching, (
        f"传递闭包连根都没算进去：{ROOTS - touching}。"
        "算法本身坏了，上面那条断言就变成了恒真。"
    )
    # 再抽查两个「必须靠传递才能算出来」的：一个直接调 get_db，
    # 一个只调前者。整个仓有几百个函数，闭包算出来的必须远多于直接调用。
    assert "list_admin_users" in touching, "直接调 get_db 的函数没被算进来"
    assert "check_quota" in touching, (
        "只调 check_quota_kind（后者才调 get_db）的函数没被算进来 —— "
        "说明闭包没算，只算了直接调用"
    )


def test_gate_actually_goes_blind_without_the_entry():
    """变异：把一个名字从 DB_FUNCS 删掉，门禁必须真的对它失明。

    这条是给上面那条断言的**判别力**做担保：如果删掉名单条目门禁照样抓得到，
    那 `DB_FUNCS` 就是摆设，上面的测试也就没有意义。
    """
    import textwrap

    from seams import close_all_thread_connections

    target = "list_admin_users"
    original = check_db_fixture.DB_FUNCS
    assert target in original, f"{target} 不在名单里，先确认基线状态"

    probe = BACKEND / "tests" / "test_zzz_dbfuncs_probe.py"
    try:
        # 一条「违规」测试：只经由 target 触库，且**不**取 db 夹具。
        # 两条纪律：
        #   1. 不能取 db —— 取了它就合规，门禁按设计跳过（DB_FIXTURE_ARGS）
        #   2. 不能直接调 get_db —— 否则门禁从 get_db 那条路径抓到，
        #      隔离不出「删掉 target 会不会失明」
        probe.write_text(
            textwrap.dedent(f"""
                import database


                def test_probe_via_{target}_only():
                    assert database.{target}(limit=1, offset=0) is not None
            """).lstrip(),
            encoding="utf-8",
        )

        # 基线：名单里有 target → 门禁应当报出这个探针
        offenders = check_db_fixture.find_offenders(probe)
        assert offenders, "探针在名单完整时都没被门禁看见 —— 检查本身失效了"
        assert any(target in calls for _, calls in offenders), (
            f"门禁报出的违规里不含 {target}：{offenders}"
        )

        # 变异：从名单里拿掉 target → 门禁应对它失明
        check_db_fixture.DB_FUNCS = original - {target}
        try:
            blind = check_db_fixture.find_offenders(probe)
            assert not blind, (
                f"把 {target} 从 DB_FUNCS 删掉之后门禁**仍然**看得见它：{blind}。"
                "说明它是通过别的路径被抓住的（多半是直接调了 get_db），"
                "这个探针隔离不出名单的作用。"
            )
        finally:
            check_db_fixture.DB_FUNCS = original
    finally:
        probe.unlink(missing_ok=True)
        close_all_thread_connections()


# ── 工单 #57：门禁自身漏掉的两个形状 ───────────────────────────
#
# 下面两条探针都写成**真的测试文件**，再交给 find_offenders 判——
# 不用伪造 AST。判别力的来源是两条探针只差一个夹具参数，
# 结果必须相反：门禁越权（把合规的也报出来）比漏报更严重，所以反向对照必须绿。

PROBE_VIA_HELPER = '''
import database


def _seed(email="x@example.com"):
    return database.create_user(email, "h")


def test_uses_helper_without_any_fixture():
    _seed()


def test_uses_helper_with_db_fixture(db):
    _seed()
'''

PROBE_VIA_CUSTOM_FIXTURE = '''
import pytest

import database
from seams import make_client


def _seed():
    return database.create_user("x@example.com", "h")


@pytest.fixture()
def my_client(db):
    """名字不在 DB_FIXTURE_ARGS 里，但它依赖 db —— 门禁必须认出来。"""
    import main as main_module

    return make_client(main_module.app)


def test_via_custom_fixture(my_client):
    _seed()
'''

PROBE_HELPER_ONLY = '''
import database


def _seed():
    return database.create_user("x@example.com", "h")


def test_ok(db):
    _seed()
'''


def _scan_source(tmp_path, source, name="test_zzz_probe.py"):
    probe = tmp_path / name
    probe.write_text(source.lstrip(), encoding="utf-8")
    return check_db_fixture.find_offenders(probe)


def test_helper_touching_the_db_is_not_invisible(tmp_path):
    """helper 触库 + 测试不取夹具 → 必须报出来，且报的是**测试名**。

    工单 #57 的由来：这个形状此前完全看不见，于是工单 #51 的测试
    把两行用户写进了真实 backend/data/app.db，而门禁全程没吭声。
    """
    offenders = _scan_source(tmp_path, PROBE_VIA_HELPER)
    names = [n for n, _ in offenders]
    assert names == ["test_uses_helper_without_any_fixture"], (
        f"应当只报出没取夹具的那条，实得 {offenders}。"
        "报出 helper 自己的名字说明门禁在拿 helper 当测试，"
        "那样给人指路时指错了地方。"
    )
    assert offenders[0][1] == ["create_user"], offenders


def test_the_same_helper_is_fine_when_the_fixture_is_taken(tmp_path):
    """反向对照：**同一个文件**里两条测试走同一个 helper，只差一个 ``db`` 参数。

    结果必须只有一条被报出来。门禁越权比漏报更严重——它会训练所有人
    给无关测试也塞一个 ``db`` 参数。

    （对照不能写成「同一个源文件扫两次」：那份源里本来就含一条违规测试，
    拿它断言 ``== []`` 是我第一版的写法，红得莫名其妙——红的是断言写错了，
    不是门禁越权。）
    """
    offenders = _scan_source(tmp_path, PROBE_VIA_HELPER)
    names = [n for n, _ in offenders]
    assert names == ["test_uses_helper_without_any_fixture"], (
        f"取了 db 夹具的那条也被报出来了：{offenders} —— 门禁越权"
    )
    assert _scan_source(tmp_path, PROBE_HELPER_ONLY) == [], \
        "整个文件都合规却被报出来了 —— 门禁越权"


def test_fixture_dependency_is_resolved_transitively(tmp_path):
    """自定义夹具名（``my_client``）依赖 db 时必须被认成合规。

    这条是工单 #57 的第二半：``DB_FIXTURE_ARGS`` 是**契约名**而不是词表。
    早先的门禁只看函数自己的参数，于是私有名 ``client_app(db)`` 会被误报，
    而唯一的「修法」是把夹具改名成词表里的名字——门禁在教人迎合词表，
    而不是陈述事实。
    """
    assert _scan_source(tmp_path, PROBE_VIA_CUSTOM_FIXTURE) == [], \
        "依赖 db 的自定义夹具没被解析出来 —— 门禁在逼人改夹具名"


def test_removing_the_test_prefix_filter_would_misattribute(tmp_path):
    """变异：拿掉 ``startswith("test_")`` 过滤 → 报出来的名字必须变。

    判别力靠**名字**：不加过滤时门禁会把 helper 自己当成一条「没取夹具的测试」，
    于是 offenders 里出现的是 ``_seed`` 而不是测试名。上面那条断言因此转红。

    这条同时说明那行过滤不是摆设：它保证报出来的是**该去修的那条测试**。
    """
    import ast as _ast

    tree = _ast.parse(PROBE_VIA_HELPER.lstrip())
    funcs = check_db_fixture._collect_functions(tree)
    fixtures = check_db_fixture._fixture_map(tree)
    fixtures.update(check_db_fixture._conftest_fixtures())

    every = []
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            continue
        args = {a.arg for a in node.args.args}
        if check_db_fixture._has_db_fixture(args, fixtures):
            continue
        calls = check_db_fixture._reachable_db(funcs, node.name)
        if calls:
            every.append((node.name, sorted(calls)))

    names = [n for n, _ in every]
    assert "test_uses_helper_without_any_fixture" in names, every
    assert "_seed" in names, (
        f"去掉 test_ 前缀过滤后应当连 helper 自己都被算进来，实得 {every}。"
        "如果这条红了，说明 _reachable_db 根本没在看 helper —— "
        "那上面三条断言全部恒真。"
    )
