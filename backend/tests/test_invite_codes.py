"""邀请码注册（工单 #99 / #103 / ADR 0023）。

## 这个文件在钉什么

公开注册从「邮箱 + 密码」改成「邮箱 + 密码 + 邀请码」。邀请码由管理员
发放，后端在**同一个事务**里完成「校验码 → 建号 → 计数」。

工单 #103 之后，邀请码**只存哈希**：库里存的是 sha256 摘要，明文只在
创建那一次响应里出现；码本身也必须够强（组合空间 ≥ 128 bit）。

断言只钉外部可观察的东西：

- HTTP 状态码与响应体里的中文 detail；
- 数据库里的真实行（used_count、users 行数、**存的是不是哈希**）；
- 数据库**文件**里有没有明文（工单 #103 的核心承诺）；
- 管理端点返回的字段形状（明文只给一次、列表脱敏）。

不钉私有函数名，不钉内部调用顺序。结构性约束（必须校验码、必须计数、
必须判过期与作废、**熵不能低于 128 bit、库里不能有明文、有效期按小时
换算**）由 ``tests/mutation_invite_codes.py`` 兜住：把实现改坏，那些
用例必须转红。

## 四类失效原因必须分开

「邀请码无效」「邀请码已作废」「邀请码已过期」「邀请码已被用完」是四句
不同的话，因为用户据此要做的事不同：无效 = 抄错了或根本没发；作废 = 管理员
收回了；过期 = 换一个；用完 = 换一个。合成一句「邀请码无效」的话，四种
情况都指向同一个动作，而其中三种那个动作是错的。

## 为什么并发那条用例直接调数据层

顺序调用两次同一枚码，测的是幂等；**并发**调用两次才测并发安全。断言落在
库里的计数上，而不是某个线程的返回值——返回值只说明那一个线程看见了什么，
库里那行才是别的线程也要面对的事实。

## 熵那条断言为什么允许读模块常量

组合空间 = 码长 × log2(字符集大小)，而「字符集多大、码多长」是这份规格的
**唯一**出处，代码里就是两个常量。断言不读它们，就没有任何东西能表达
「这个码猜不中」——所以这里读常量，但**独立重算**一遍（不抄
``INVITE_CODE_ENTROPY_BITS`` 的值），并在旁边钉住「字符集没有重复字符」
（重复字符会悄悄把熵打折，而字符集长什么样肉眼看不出来）。
"""
import ast
import hashlib
import math
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import auth
import database
from seams import auth_headers, make_client

#: invite_codes 的列集。**全新库**与**老库升级后**必须是同一份。
#:
#: 写成常量而不是「比两遍」：比两遍只能保证两个库一致，保证不了它们
#: 一致于设计。两处 DDL 各写一遍（init_db 的 executescript 与升级路径），
#: 漂移的后果是老库缺列而新库不缺——症状是「老用户升级后注册报 no such
#: column」，而新装的人一切正常。
EXPECTED_COLUMNS = {
    "id",
    "code_hash",
    "code_hint",
    "created_by",
    "created_at",
    "expires_at",
    "max_uses",
    "used_count",
    "revoked",
    "note",
}

#: 工单 #103 之前的老表结构（**冻结副本**）。升级路径的测试用它造一个
#: 「真老库」：``code`` 存明文，没有 ``code_hash`` / ``code_hint``。
#:
#: 为什么是冻结副本而不是从当前代码里取：当前代码里**已经没有**这个结构
#: 了，要造出「真老库」只能把老结构原样写在这里。老结构改了名字或列，
#: 这个副本要跟着改——它就是「老库长什么样」的定义。
LEGACY_INVITE_CODES_DDL = """
    CREATE TABLE invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        max_uses INTEGER NOT NULL DEFAULT 1,
        used_count INTEGER NOT NULL DEFAULT 0,
        revoked INTEGER NOT NULL DEFAULT 0,
        note TEXT
    )
"""


# ── 夹具与助手 ────────────────────────────────────────────


@pytest.fixture()
def client_app(db, make_user):
    """真实 app + 真实 client + 一个管理员、一个普通用户。

    刻意不 ``with client``：lifespan 里的 init_db() 会把「老库升级」
    那几条断言遮住（与 tests/test_admin_api.py 同一理由）。
    """
    import main as main_module

    admin_id = make_user(email="boss@example.com")
    plain_id = make_user(email="plain@example.com")
    with database.get_db() as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin_id,))
    return make_client(main_module.app), admin_id, plain_id


def _admin_hdr(client_app):
    _, admin_id, _ = client_app
    return auth_headers(auth.create_token(admin_id, "boss@example.com"))


def _plain_hdr(client_app):
    _, _, plain_id = client_app
    return auth_headers(auth.create_token(plain_id, "plain@example.com"))


def _register(client, email="new@example.com", password="secret1", code=None):
    """打注册端点。``code=None`` 表示**根本不带** invite_code 这个键。"""
    body = {"email": email, "password": password}
    if code is not None:
        body["invite_code"] = code
    return client.post("/api/auth/register", json=body)


def _sha256(code: str) -> str:
    """sha256(明文码)，**测试自己算**。

    不调后端的哈希函数：那样测的是「后端算的哈希 == 后端算的哈希」，
    恒真。这里独立算一遍，钉住的是「库里那列**就是** sha256(明文)」。
    """
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _used_count(code):
    """库里这枚码的已用数（按哈希查，工单 #103 之后库里没有明文）。"""
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT used_count FROM invite_codes WHERE code_hash = ?", (_sha256(code),)
        ).fetchone()
    return None if row is None else row["used_count"]


def _db_file_bytes() -> bytes:
    """数据库**文件**的字节（含 WAL）。

    为什么读文件而不只读行：工单 #103 的承诺是「库被读走不等于码可用」，
    而「库里没有明文」如果只看行，会漏掉两处——表外的空闲页里可能还留着
    旧明文，WAL 里也可能留着。读文件才是攻击者拿到的东西。
    """
    path = Path(database.get_db_path())
    blobs = [path.read_bytes()] if path.exists() else []
    wal = path.with_name(path.name + "-wal")
    if wal.exists():
        blobs.append(wal.read_bytes())
    blob = b"".join(blobs)
    assert blob, (
        f"数据库文件读不到内容（{path}）："
        "「文件里没有明文」这条断言就是空的，什么都没验"
    )
    return blob


def _checkpoint_the_database():
    """把 WAL 里的内容落回主库文件，并把 WAL 截断。

    为什么需要：WAL 模式下主库文件是**旧的**（最新内容在 WAL 里），WAL
    文件自己也可能留着旧页。只读主库文件、或者只读 WAL，都会漏掉一半
    真相——「文件里有没有明文」这条断言必须先把两边对齐。
    """
    with database.get_db() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _dotted_names(node):
    """把一个 AST 节点摊成点号串集合（``secrets.choice`` 这种）。"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            bases = _dotted_names(child.value)
            attr = child.attr
            if bases:
                names |= {f"{base}.{attr}" for base in bases}
            else:
                names.add(attr)
        elif isinstance(child, ast.Call):
            names |= _dotted_names(child.func)
    return names


def _function_source(function_name: str) -> ast.FunctionDef:
    """从**磁盘上的**源码里取出某个函数的 AST。

    读磁盘而不是读内存里的模块对象：变异脚本改的就是磁盘上的文件，
    读内存会看不到被改坏的那份（断言就永远绿着）。

    用 AST 而不是纯文本找（仓库的测试策略 §6）：文本断言挡不住等价改写，
    作用域限定到**这一个函数**之后才是「结构性判据」而不是「关键字守卫」。
    """
    source = Path(database.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return node
    raise AssertionError(f"backend/database.py 里找不到函数 {function_name}")


def _user_count(email):
    """库里这个邮箱有几行。"""
    with database.get_db() as conn:
        return conn.execute(
            "SELECT count(*) FROM users WHERE email = ?", (email,)
        ).fetchone()[0]


def _past_timestamp():
    """一小时前的 UTC ISO-8601。"""
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _future_timestamp():
    """一小时后的 UTC ISO-8601。"""
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def _race(fn, count):
    """让 ``count`` 个线程同时起跑，返回每个线程的 ``("ok", 值)`` 或 ``("err", 异常)``。

    屏障是关键：没有它，线程一个接一个跑，测到的是顺序执行而不是并发。
    """
    barrier = threading.Barrier(count)
    results = [None] * count

    def _run(index):
        barrier.wait()
        try:
            results[index] = ("ok", fn(index))
        except Exception as exc:  # noqa: BLE001 - 这里就是要收住失败
            results[index] = ("err", exc)

    threads = [threading.Thread(target=_run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# ── 注册闸门：没有可用邀请码就进不来 ──────────────────────


class TestRegisterNeedsAUsableInviteCode:

    def test_registering_without_a_code_is_refused(self, client_app):
        client, _, _ = client_app
        r = _register(client)
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "需要邀请码"
        # 反向确认：拒绝之后账号真的没建
        assert _user_count("new@example.com") == 0

    def test_registering_with_a_blank_code_is_refused(self, client_app):
        client, _, _ = client_app
        r = _register(client, code="   ")
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "需要邀请码"
        assert _user_count("new@example.com") == 0

    def test_an_unknown_code_is_refused(self, client_app):
        client, _, _ = client_app
        r = _register(client, code="NOSUCHCODE12")
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "邀请码无效"
        assert _user_count("new@example.com") == 0

    def test_a_revoked_code_is_refused(self, client_app):
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1)
        assert database.revoke_invite_code(item["id"]) == 1
        r = _register(client, code=item["code"])
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "邀请码已作废"
        assert _user_count("new@example.com") == 0

    def test_an_expired_code_is_refused(self, client_app):
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1, expires_at=_past_timestamp())
        r = _register(client, code=item["code"])
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "邀请码已过期"
        assert _user_count("new@example.com") == 0

    def test_an_exhausted_code_is_refused(self, client_app):
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1)
        first = _register(client, email="first@example.com", code=item["code"])
        assert first.status_code == 200, f"实得 {first.status_code}：{first.text}"
        r = _register(client, email="second@example.com", code=item["code"])
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "邀请码已被用完"
        assert _user_count("second@example.com") == 0

    def test_the_four_failure_reasons_are_four_different_sentences(self, client_app):
        """四类原因必须是四句不同的话——用户据此才知道该做什么。

        合成一句的话，四个用例（各自钉住一句）会一起红；这条把「四句」
        这个整体判据也钉住：将来有人把其中一句改成别的说法时，这里
        报出的是「四类原因混了」，而不是四个用例各报一个看不懂的差异。
        """
        client, _, _ = client_app
        unknown = _register(client, code="NOSUCHCODE12").json()["detail"]

        revoked_item = database.create_invite_code(max_uses=1)
        database.revoke_invite_code(revoked_item["id"])
        revoked = _register(client, code=revoked_item["code"]).json()["detail"]

        expired_item = database.create_invite_code(
            max_uses=1, expires_at=_past_timestamp()
        )
        expired = _register(client, code=expired_item["code"]).json()["detail"]

        spent_item = database.create_invite_code(max_uses=1)
        _register(client, email="one@example.com", code=spent_item["code"])
        exhausted = _register(client, email="two@example.com", code=spent_item["code"]).json()["detail"]

        assert {unknown, revoked, expired, exhausted} == {
            "邀请码无效",
            "邀请码已作废",
            "邀请码已过期",
            "邀请码已被用完",
        }, f"四类原因没有说成四句可行动的话：{unknown} / {revoked} / {expired} / {exhausted}"


# ── 消费：码被用掉，账号建成 ──────────────────────────────


class TestRegisterConsumesTheCode:

    def test_a_valid_code_registers_the_account_and_counts_one_use(self, client_app):
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1)
        r = _register(client, email="new@example.com", code=item["code"])
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        body = r.json()
        assert body["success"] is True
        assert body["data"]["user"]["email"] == "new@example.com"
        assert _user_count("new@example.com") == 1
        assert _used_count(item["code"]) == 1

    def test_the_code_is_not_consumed_when_the_email_already_exists(self, client_app):
        """注册失败时**码不能被消耗**。

        消耗了却没建出账号，用户白丢一个名额——而他什么也没做错。
        """
        client, _, _ = client_app
        database.create_user("taken@example.com", "hash")
        item = database.create_invite_code(max_uses=1)
        r = _register(client, email="taken@example.com", code=item["code"])
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "该邮箱已注册"
        assert _used_count(item["code"]) == 0, "注册失败了却把码消耗了"

    def test_registering_the_same_email_twice_does_not_consume_a_second_code(self, client_app):
        """同一个邮箱重复注册，**第二枚码**不能被消耗。

        「重复注册」有两种歪账：第二枚码被吃掉（用户白丢），或第一个账号
        被顶掉（用户丢账号）。这里两样都钉住。
        """
        client, _, _ = client_app
        first = database.create_invite_code(max_uses=1)
        second = database.create_invite_code(max_uses=1)

        assert _register(client, email="dup@example.com", code=first["code"]).status_code == 200
        assert _used_count(first["code"]) == 1

        r = _register(client, email="dup@example.com", code=second["code"])
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert r.json()["detail"] == "该邮箱已注册"
        assert _used_count(second["code"]) == 0, "第二枚码被消耗了"
        assert _user_count("dup@example.com") == 1


# ── 并发：同一枚码只能被一个请求抢到 ──────────────────────


class TestConcurrentUseOfOneCode:

    def test_two_registrations_racing_for_one_shot_code_only_one_wins(self, db):
        """两个请求抢同一枚 max_uses=1 的码，**只能成功一个**。

        断言落在库里的计数与 users 行数上：两个账号、一个名额。
        """
        item = database.create_invite_code(max_uses=1)

        def _register_one(index):
            return database.register_with_invite_code(
                f"race{index}@example.com", "hash", item["code"]
            )

        results = _race(_register_one, 2)
        winners = [r for r in results if r[0] == "ok"]
        losers = [r for r in results if r[0] == "err"]

        assert len(winners) == 1, f"两个请求都注册成功了：{results}"
        assert len(losers) == 1, f"只有一个请求该失败：{results}"
        assert isinstance(losers[0][1], database.InviteRegistrationError), (
            f"失败的原因不是邀请码问题：{losers[0][1]!r}"
        )
        assert losers[0][1].detail == "邀请码已被用完"
        assert _used_count(item["code"]) == 1, "计数不是 1"
        with database.get_db() as conn:
            accounts = conn.execute(
                "SELECT count(*) FROM users WHERE email LIKE 'race%@example.com'"
            ).fetchone()[0]
        assert accounts == 1, f"建出了 {accounts} 个账号"

    def test_a_code_with_several_uses_can_be_won_by_several_threads(self, db):
        """反向对照：名额够时，几个请求都能抢到。

        只钉「只能成功一个」的话，把 max_uses 也改成 1 的实现同样能绿——
        那就是名额语义坏了。这条钉住「名额够就都放行」。
        """
        item = database.create_invite_code(max_uses=3)

        def _register_one(index):
            return database.register_with_invite_code(
                f"many{index}@example.com", "hash", item["code"]
            )

        results = _race(_register_one, 3)
        winners = [r for r in results if r[0] == "ok"]
        assert len(winners) == 3, f"名额有 3 个却只放行了 {len(winners)} 个：{results}"
        assert _used_count(item["code"]) == 3


# ── 管理端：发码、列表脱敏、作废 ──────────────────────────


class TestAdminInviteCodeEndpoints:

    def test_a_plain_user_cannot_issue_a_code(self, client_app):
        client, _, _ = client_app
        r = client.post("/api/admin/invite-codes", json={}, headers=_plain_hdr(client_app))
        assert r.status_code == 403, f"实得 {r.status_code}：{r.text}"
        with database.get_db() as conn:
            assert conn.execute("SELECT count(*) FROM invite_codes").fetchone()[0] == 0

    def test_a_plain_user_cannot_list_codes(self, client_app):
        client, _, _ = client_app
        r = client.get("/api/admin/invite-codes", headers=_plain_hdr(client_app))
        assert r.status_code == 403, f"实得 {r.status_code}：{r.text}"

    def test_a_plain_user_cannot_revoke_a_code(self, client_app):
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1)
        r = client.post(
            f"/api/admin/invite-codes/{item['id']}/revoke", headers=_plain_hdr(client_app)
        )
        assert r.status_code == 403, f"实得 {r.status_code}：{r.text}"
        # 反向确认：403 的请求没有把码作废掉
        assert database.get_invite_code_by_id(item["id"])["revoked"] == 0

    def test_issuing_a_code_returns_the_plaintext_code_once(self, client_app):
        client, admin_id, _ = client_app
        r = client.post("/api/admin/invite-codes", json={}, headers=_admin_hdr(client_app))
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        item = r.json()["item"]
        assert isinstance(item["code"], str) and len(item["code"]) >= 8, (
            f"创建响应里没有可用的明文码：{item}"
        )
        assert item["max_uses"] == 1
        assert item["used_count"] == 0
        assert item["revoked"] == 0
        assert item["expires_at"] is None
        assert item["created_by"] == admin_id

    def test_the_list_never_echoes_the_full_code(self, client_app):
        """列表**不回显完整码**：明文只在创建响应里给那一次。

        不回显的理由：后台页面是「所有人都有的一把注册钥匙」的表——看过
        一眼就能拿去注册任意账号。
        """
        client, _, _ = client_app
        created = client.post(
            "/api/admin/invite-codes", json={}, headers=_admin_hdr(client_app)
        ).json()["item"]

        r = client.get("/api/admin/invite-codes", headers=_admin_hdr(client_app))
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        body = r.json()
        assert body["total"] == 1
        row = body["items"][0]
        assert "code" not in row, f"列表里回显了完整码：{row}"
        assert row["code_masked"] == created["code"][:4] + "****" + created["code"][-4:]
        assert created["code"] not in r.text, "响应体里出现了完整码"

    def test_the_list_tells_the_admin_whether_a_code_is_usable(self, client_app):
        """列表要给管理员三样东西：还能不能用、还能用几次、要不要作废。"""
        client, _, _ = client_app
        spent = database.create_invite_code(max_uses=1)
        _register(client, email="used@example.com", code=spent["code"])
        revoked = database.create_invite_code(max_uses=1)
        database.revoke_invite_code(revoked["id"])
        expired = database.create_invite_code(
            max_uses=1, expires_at=_past_timestamp()
        )

        r = client.get("/api/admin/invite-codes", headers=_admin_hdr(client_app))
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        rows = {row["id"]: row for row in r.json()["items"]}

        assert rows[spent["id"]]["used_count"] == 1
        assert rows[spent["id"]]["exhausted"] is True
        assert rows[spent["id"]]["status"] == "exhausted"

        assert rows[revoked["id"]]["revoked"] == 1
        assert rows[revoked["id"]]["status"] == "revoked"

        assert rows[expired["id"]]["expired"] is True
        assert rows[expired["id"]]["status"] == "expired"

    def test_revoking_a_code_makes_it_unusable(self, client_app):
        client, _, _ = client_app
        created = client.post(
            "/api/admin/invite-codes", json={}, headers=_admin_hdr(client_app)
        ).json()["item"]
        r = client.post(
            f"/api/admin/invite-codes/{created['id']}/revoke",
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        assert r.json()["item"]["revoked"] == 1

        reg = _register(client, code=created["code"])
        assert reg.status_code == 400, f"实得 {reg.status_code}：{reg.text}"
        assert reg.json()["detail"] == "邀请码已作废"

    def test_revoking_twice_is_not_an_error(self, client_app):
        """管理员点两下不是错误：作废是幂等的。"""
        client, _, _ = client_app
        created = client.post(
            "/api/admin/invite-codes", json={}, headers=_admin_hdr(client_app)
        ).json()["item"]
        path = f"/api/admin/invite-codes/{created['id']}/revoke"
        assert client.post(path, headers=_admin_hdr(client_app)).status_code == 200
        assert client.post(path, headers=_admin_hdr(client_app)).status_code == 200

    def test_revoking_an_unknown_code_is_404(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes/999999/revoke", headers=_admin_hdr(client_app)
        )
        assert r.status_code == 404, f"实得 {r.status_code}：{r.text}"

    def test_a_code_issued_with_several_uses_can_be_used_that_many_times(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes", json={"max_uses": 3}, headers=_admin_hdr(client_app)
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        item = r.json()["item"]
        assert item["max_uses"] == 3

        for index in range(3):
            got = _register(client, email=f"multi{index}@example.com", code=item["code"])
            assert got.status_code == 200, f"第 {index + 1} 次实得 {got.status_code}：{got.text}"
        assert _used_count(item["code"]) == 3

        fourth = _register(client, email="multi3@example.com", code=item["code"])
        assert fourth.status_code == 400, f"实得 {fourth.status_code}：{fourth.text}"
        assert fourth.json()["detail"] == "邀请码已被用完"

    def test_a_code_issued_with_an_expiry_is_not_expired_yet(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes",
            json={"expires_in_hours": 24},
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        item = r.json()["item"]
        assert item["expires_at"] is not None
        assert item["expired"] is False
        assert _register(client, code=item["code"]).status_code == 200

    def test_a_code_issued_without_an_expiry_never_expires(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes", json={}, headers=_admin_hdr(client_app)
        )
        item = r.json()["item"]
        assert item["expires_at"] is None
        assert item["expired"] is False

    def test_zero_uses_is_refused(self, client_app):
        """「可用 0 次」发出去就是一枚立刻作废的码——那是传参错了。"""
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes", json={"max_uses": 0}, headers=_admin_hdr(client_app)
        )
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert "max_uses" in r.json()["detail"]

    def test_zero_expiry_is_refused(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes",
            json={"expires_in_hours": 0},
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 400, f"实得 {r.status_code}：{r.text}"
        assert "expires_in_hours" in r.json()["detail"]

    def test_an_unknown_field_is_refused(self, client_app):
        """传了没声明的键要报错，而不是被静默丢掉。

        静默丢掉的后果：管理员以为「可用 2 次」生效了，实际发出去的是
        默认的一次性码。
        """
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes", json={"max_use": 2}, headers=_admin_hdr(client_app)
        )
        assert r.status_code == 422, f"实得 {r.status_code}：{r.text}"

    def test_a_note_is_kept(self, client_app):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes",
            json={"note": "给张三"},
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        assert r.json()["item"]["note"] == "给张三"


# ── 老库升级 ──────────────────────────────────────────────


class TestLegacyDatabaseUpgrade:

    def test_a_fresh_database_has_the_invite_codes_table(self, db):
        with database.get_db() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(invite_codes)")
            }
        assert columns == EXPECTED_COLUMNS, f"全新库的列集不对：{sorted(columns)}"

    def test_a_legacy_database_without_the_table_gets_it(self, legacy_db):
        """从**真缺这张表**的老库启动，表要自己长出来。

        老库是老库：表压根不存在。这条路径不走 executescript 那句
        ``CREATE TABLE IF NOT EXISTS`` 也得能起来——症状是「注册时报 no
        such table」，而它发生在用户注册的那一刻，不是启动的那一刻。
        """
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")

        # 前提自检：老库真的没有这张表
        with database.get_db() as conn:
            present = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='invite_codes'"
            ).fetchone()[0]
        assert present == 0, "前提不成立：这张表还在，测不到升级路径"

        database.init_db()

        with database.get_db() as conn:
            present = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='invite_codes'"
            ).fetchone()[0]
        assert present == 1, "老库启动后仍然没有 invite_codes 表"

    def test_the_upgraded_columns_match_the_fresh_ones(self, legacy_db):
        """老库升级后的列集必须与全新库完全一致。"""
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")
        database.init_db()

        with database.get_db() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(invite_codes)")
            }
        assert columns == EXPECTED_COLUMNS, f"升级后的列集不对：{sorted(columns)}"

    def test_the_upgrade_is_idempotent(self, legacy_db):
        """升级跑两遍，结果与跑一遍一样。

        init_db() 每次启动都跑——不幂等的话，第二次启动就会把表改坏。
        """
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")
        database.init_db()
        database.init_db()

        with database.get_db() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(invite_codes)")
            }
        assert columns == EXPECTED_COLUMNS, f"升级跑两遍后列集不对：{sorted(columns)}"

    def test_a_table_missing_a_column_gets_it_back(self, legacy_db):
        """表在、但列不全（上一版发布时少写了列）时，补列要跟上。"""
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")
            conn.execute(
                "CREATE TABLE invite_codes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "code TEXT NOT NULL UNIQUE, "
                "created_by INTEGER)"
            )

        database.init_db()

        with database.get_db() as conn:
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(invite_codes)")
            }
        assert EXPECTED_COLUMNS <= columns, f"升级后还缺列：{sorted(EXPECTED_COLUMNS - columns)}"

    def test_an_upgraded_database_can_actually_register(self, legacy_db):
        """升级不只是「表在」，还得能用：老库升级后能发码、能注册。"""
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")
        database.init_db()

        item = database.create_invite_code(max_uses=1)
        registered = database.register_with_invite_code(
            "after-upgrade@example.com", "hash", item["code"]
        )
        assert registered["email"] == "after-upgrade@example.com"
        assert _used_count(item["code"]) == 1


# ── 工单 #103：强度 / 只存哈希 / 有效期换算 ──────────────────


class TestTheCodeSpaceIsBigEnough:
    """邀请码必须**猜不中**（工单 #103）。

    组合空间 = 码长 × log2(字符集大小)。这个数低于 128 bit 时，库被读走
    之后攻击者可以离线把整个空间枚举一遍——那时「只存哈希」这层保护挡不住
    任何东西（sha256 再快也只是每条候选一次哈希）。
    """

    def test_the_code_space_has_at_least_128_bits(self):
        """组合空间的下界：≥ 128 bit。

        数字**不写死**：写死一个数（"至少 20 位"）的话，把字符集改小一半
        断言还在绿。这里按「码长 × log2(字符集大小)」现算，字符集和码长
        一动，这个数跟着动。
        """
        alphabet = database.INVITE_CODE_ALPHABET
        length = database.INVITE_CODE_LENGTH
        bits = length * math.log2(len(alphabet))
        assert bits >= 128, (
            f"邀请码的组合空间只有 {bits:.1f} bit"
            f"（{length} 位 × 字符集 {len(alphabet)} 个），低于 128 bit："
            "库泄露之后可以被离线穷举"
        )

    def test_the_alphabet_has_no_duplicate_characters(self):
        """重复字符会**悄悄**把熵打折，而字符集长什么样肉眼看不出来。

        同一个字符出现两次，组合空间就少了一截——`math.log2(len(...))`
        看的是**长度**不是**去重后的个数**，所以这一条得单独钉。
        """
        alphabet = database.INVITE_CODE_ALPHABET
        repeated = sorted({c for c in alphabet if alphabet.count(c) > 1})
        assert not repeated, f"字符集里有重复字符：{repeated}"

    def test_generated_codes_come_from_a_cryptographic_source(self):
        """码必须来自密码学随机源。

        这条**只能**看实现：`random.choice` 出来的码和 `secrets.choice`
        出来的码在行为上没有区别，但前者的序列是可以被预测的——被预测的
        邀请码等于没有邀请码。判据用 AST 限定到 `generate_invite_code`
        这一个函数（仓库测试策略 §6：文本断言要么用 AST / 函数归属限定
        作用域，要么它拦不住等价改写）。

        判的是「用没用 ``secrets``」，不是「有没有恰好写成
        ``secrets.choice``」——``secrets.SystemRandom().choice`` 同样是
        密码学随机源，把它当成缺陷就是误报。
        """
        names = _dotted_names(_function_source("generate_invite_code"))
        assert any(name.startswith("secrets.") for name in names), (
            "generate_invite_code 没用密码学随机源 secrets.*，"
            f"现在用到的随机源是：{sorted(names)}"
        )

    def test_a_lot_of_codes_are_all_different(self, db):
        """发 200 枚码，不能有重复。

        顺带钉住「每个字符都真被用到」：生成器若只用了字符集的一部分
        （比如把数字那一半写死），码长不变但熵会缩水——而组合空间那条
        断言看的是字符集长度，看不出「这些字符其实没被用过」。
        """
        codes = [database.generate_invite_code() for _ in range(200)]
        assert len(set(codes)) == len(codes), "生成了重复的邀请码"
        unused = sorted(set(database.INVITE_CODE_ALPHABET) - set("".join(codes)))
        assert not unused, f"字符集里有字符一次都没被用到：{unused}"


class TestOnlyTheHashIsStored:
    """工单 #103 的核心承诺：库被读走 ≠ 码被用掉。

    旧实现把明文存库，库一泄露，在用的码就全成了明文——不需要爆破，
    不需要任何算力。改完之后库里只有 sha256 摘要，明文只在创建那一次
    响应里出现。
    """

    def test_the_row_stores_the_hash_and_not_the_plaintext(self, db):
        item = db.create_invite_code(max_uses=1)
        code = item["code"]
        with db.get_db() as conn:
            row = conn.execute("SELECT * FROM invite_codes").fetchone()
        assert row["code_hash"] == _sha256(code), "存的不是 sha256(明文)"
        for key in row.keys():
            assert row[key] != code, f"第 {key} 列存了明文邀请码"

    def test_the_database_file_holds_no_plaintext(self, db):
        """攻击者拿到的是**文件**，不是「表里的行」。

        只查行会漏两处：表外的空闲页（老表被替换之后残留）与 WAL。
        所以这条读的是文件本身。
        """
        item = db.create_invite_code(max_uses=1)
        code = item["code"]
        _checkpoint_the_database()
        blob = _db_file_bytes()
        assert code.encode("utf-8") not in blob, "数据库文件里能翻出明文邀请码"

    def test_the_plaintext_leaves_only_in_the_creation_response(self, client_app):
        """明文只在创建那一次响应里出现，之后哪儿都取不回。"""
        client, _, _ = client_app
        hdr = _admin_hdr(client_app)
        created = client.post("/api/admin/invite-codes", json={}, headers=hdr)
        assert created.status_code == 200, created.text
        code = created.json()["item"]["code"]

        listing = client.get("/api/admin/invite-codes", headers=hdr)
        assert listing.status_code == 200
        assert code not in listing.text, "列表响应里出现了完整码"

        _checkpoint_the_database()
        assert code.encode("utf-8") not in _db_file_bytes(), "数据库文件里能翻出明文"

    def test_the_hint_cannot_be_used_to_register(self, client_app):
        """列表里的脱敏提示**不是码**，拿它注册必须失败。

        管理员靠它认出「这枚是我发给张三的那枚」，但它不能当码用——
        否则「库里不存明文」就只剩一半：攻击者从提示里拼不出码，
        可管理员能，而管理员能就等于这个接口能。
        """
        client, _, _ = client_app
        hdr = _admin_hdr(client_app)
        created = client.post("/api/admin/invite-codes", json={}, headers=hdr)
        assert created.status_code == 200, created.text
        listing = client.get("/api/admin/invite-codes", headers=hdr)
        row = listing.json()["items"][0]
        assert "****" in row["code_masked"], f"脱敏提示形态不对：{row['code_masked']}"

        r = _register(client, email="hint@example.com", code=row["code_masked"])
        assert r.status_code == 400, f"脱敏提示居然能注册：{r.status_code} {r.text}"
        assert r.json()["detail"] == "邀请码无效"

    def test_the_hint_is_stored_in_the_row(self, db):
        """提示列是**建码那一刻**存下的：它从明文算得出，从哈希算不出。"""
        item = db.create_invite_code(max_uses=1)
        code = item["code"]
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT code_hint FROM invite_codes WHERE code_hash = ?",
                (_sha256(code),),
            ).fetchone()
        assert row["code_hint"] == code[:4] + "****" + code[-4:]


class TestTheExpiryIsCountedInHours:
    """「有效小时数」就是小时（工单 #103）。

    旧实现把 ``timedelta(hours=hours)`` 写成 ``timedelta(minutes=hours)``，
    38 条用例照样全绿——「可配置有效期」这条当时没有任何判据。
    """

    def test_twenty_four_hours_means_twenty_four_hours(self, client_app, frozen_clock):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes",
            json={"max_uses": 1, "expires_in_hours": 24},
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        item = r.json()["item"]
        created = datetime.fromisoformat(item["created_at"])
        expires = datetime.fromisoformat(item["expires_at"])
        assert expires - created == timedelta(hours=24), (
            f"选 24 小时，库里差的却是 {expires - created}："
            "有效期的单位换算错了（小时被当成了分钟？）"
        )

    def test_one_hour_means_one_hour(self, client_app, frozen_clock):
        client, _, _ = client_app
        r = client.post(
            "/api/admin/invite-codes",
            json={"max_uses": 1, "expires_in_hours": 1},
            headers=_admin_hdr(client_app),
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        item = r.json()["item"]
        created = datetime.fromisoformat(item["created_at"])
        expires = datetime.fromisoformat(item["expires_at"])
        assert expires - created == timedelta(hours=1), (
            f"选 1 小时，库里差的却是 {expires - created}"
        )


class TestUpgradingFromAnOldPlaintextDatabase:
    """从**真老库**（``code`` 存明文）启动，要能升级成新结构（工单 #103）。

    三件事必须同时成立：升级前发出的码**仍然可用**；升级后**表里**没有
    明文；升级后**文件里**也没有明文。
    """

    OLD_CODE = "OLDLEGACYCODE1234567890AB"

    def _build_an_old_database(self):
        """把当前库改成工单 #103 之前的样子，并塞进一枚明文邀请码。

        先 ``init_db()`` 造出完整的库（users 等表要留着，否则注册无从
        谈起），再把 invite_codes 换成老结构、塞进明文行——这就是真老库
        的样子：**表在，列是旧的，行里是明文**。
        """
        database.init_db()
        with database.get_db() as conn:
            conn.execute("DROP TABLE invite_codes")
            conn.execute(LEGACY_INVITE_CODES_DDL)
            conn.execute(
                "INSERT INTO invite_codes (id, code, created_by, created_at, expires_at,"
                " max_uses, used_count, revoked, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, self.OLD_CODE, None, "2026-01-01T00:00:00+00:00", None, 2, 1, 0, "老码"),
            )

    def test_an_old_code_still_works_after_the_upgrade(self, legacy_db):
        """升级前发出的码，升级后**仍然可用**。

        迁移时把明文算成哈希填进新表，所以老码还能查到、还能用。
        """
        self._build_an_old_database()
        database.init_db()

        registered = database.register_with_invite_code(
            "old-code@example.com", "hash", self.OLD_CODE
        )
        assert registered["email"] == "old-code@example.com"
        assert _used_count(self.OLD_CODE) == 2, "老码的已用数没有接着往上走"

    def test_the_upgraded_table_holds_no_plaintext(self, legacy_db):
        self._build_an_old_database()
        database.init_db()

        with database.get_db() as conn:
            row = conn.execute("SELECT * FROM invite_codes").fetchone()
        assert row is not None, "升级把老码弄丢了"
        assert row["code_hash"] == _sha256(self.OLD_CODE), "老码没有被算成哈希"
        for key in row.keys():
            assert row[key] != self.OLD_CODE, f"第 {key} 列还留着明文"

    def test_the_upgraded_database_file_holds_no_plaintext(self, legacy_db):
        """文件里也不能有明文。

        老表被替换掉之后，明文可能还留在空闲页里——「库被读走等于码被用掉」
        这条承诺对老库必须是**真的**，所以升级要顺手把文件重写一遍。
        """
        self._build_an_old_database()
        database.init_db()
        _checkpoint_the_database()
        blob = _db_file_bytes()
        assert self.OLD_CODE.encode("utf-8") not in blob, (
            "老库的明文还留在数据库文件里（空闲页或 WAL）"
        )

    def test_the_upgraded_row_keeps_its_usage_and_note(self, legacy_db):
        """升级不能把老行的其它信息弄丢：已用数、备注、创建时间。"""
        self._build_an_old_database()
        database.init_db()

        with database.get_db() as conn:
            row = conn.execute("SELECT * FROM invite_codes").fetchone()
        assert row["used_count"] == 1
        assert row["max_uses"] == 2
        assert row["note"] == "老码"
        assert row["created_at"] == "2026-01-01T00:00:00+00:00"

    def test_the_new_codes_after_the_upgrade_are_still_hashed(self, legacy_db):
        """升级之后新发的码当然也是哈希——两条路径要一致。"""
        self._build_an_old_database()
        database.init_db()

        item = database.create_invite_code(max_uses=1)
        with database.get_db() as conn:
            row = conn.execute(
                "SELECT code_hash FROM invite_codes WHERE code_hash = ?",
                (_sha256(item["code"]),),
            ).fetchone()
        assert row is not None, "升级之后新建的码没有按哈希存"


# ── 既有行为不被破坏 ──────────────────────────────────────


class TestTheExistingBehaviourIsIntact:

    def test_login_still_works(self, client_app):
        """邀请码只管注册，登录不收它。"""
        client, _, _ = client_app
        item = database.create_invite_code(max_uses=1)
        assert _register(
            client, email="login@example.com", password="secret1", code=item["code"]
        ).status_code == 200

        r = client.post(
            "/api/auth/login", json={"email": "login@example.com", "password": "secret1"}
        )
        assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
        assert r.json()["data"]["user"]["email"] == "login@example.com"

    def test_the_register_endpoint_still_has_its_rate_limit(self, client_app, monkeypatch):
        """速率闸门没被顺手摘掉（工单 #99 要求保留它）。

        闸门在 handler **之前**跑：第一次请求哪怕被邀请码拒了，也已经
        计过一次——这正是「同一个来源一分钟最多 5 次」的含义。
        """
        import ratelimit

        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_REGISTER", "1")
        ratelimit.reset_counters()
        try:
            client, _, _ = client_app
            first = _register(client, code="NOSUCHCODE12")
            second = _register(client, code="NOSUCHCODE12")
            assert first.status_code != 429, f"第一次就被限流了：{first.text}"
            assert second.status_code == 429, f"第二次没被限流：{second.status_code} {second.text}"
        finally:
            ratelimit.reset_counters()

    def test_the_existing_admin_endpoints_still_work(self, client_app):
        client, _, _ = client_app
        hdr = _admin_hdr(client_app)
        for path in ("/api/admin/users", "/api/admin/community", "/api/admin/models"):
            r = client.get(path, headers=hdr)
            assert r.status_code == 200, f"{path} 实得 {r.status_code}：{r.text}"

    def test_the_quota_semantics_are_untouched(self, client_app):
        """额度语义与邀请码无关，不能被这次改动带歪。

        走公开的数据层出口（``check_quota_kind``）而不是读列：读列会把
        「额度」绑死在列名上，而额度早就拆成了解析与对话两路。
        """
        _, admin_id, _ = client_app
        allowed, remaining = database.check_quota_kind(admin_id, "parse")
        assert allowed is True
        assert remaining == database.DAILY_PARSE_LIMIT
        allowed, remaining = database.check_quota_kind(admin_id, "chat")
        assert allowed is True
        assert remaining == database.DAILY_CHAT_LIMIT
