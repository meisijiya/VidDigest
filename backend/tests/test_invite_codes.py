"""邀请码注册（工单 #99 / ADR 0023）。

## 这个文件在钉什么

公开注册从「邮箱 + 密码」改成「邮箱 + 密码 + 邀请码」。邀请码由管理员
发放，后端在**同一个事务**里完成「校验码 → 建号 → 计数」。

断言只钉外部可观察的东西：

- HTTP 状态码与响应体里的中文 detail；
- 数据库里的真实行（used_count、users 行数）；
- 管理端点返回的字段形状（明文只给一次、列表脱敏）。

不钉私有函数名，不钉内部调用顺序。结构性约束（必须校验码、必须计数、
必须判过期与作废）由 ``tests/mutation_invite_codes.py`` 兜住：把实现改坏，
那些用例必须转红。

## 四类失效原因必须分开

「邀请码无效」「邀请码已作废」「邀请码已过期」「邀请码已被用完」是四句
不同的话，因为用户据此要做的事不同：无效 = 抄错了或根本没发；作废 = 管理员
收回了；过期 = 换一个；用完 = 换一个。合成一句「邀请码无效」的话，四种
情况都指向同一个动作，而其中三种那个动作是错的。

## 为什么并发那条用例直接调数据层

顺序调用两次同一枚码，测的是幂等；**并发**调用两次才测并发安全。断言落在
库里的计数上，而不是某个线程的返回值——返回值只说明那一个线程看见了什么，
库里那行才是别的线程也要面对的事实。
"""
import threading
from datetime import datetime, timedelta, timezone

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
    "code",
    "created_by",
    "created_at",
    "expires_at",
    "max_uses",
    "used_count",
    "revoked",
    "note",
}


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


def _used_count(code):
    """库里这枚码的已用数。"""
    with database.get_db() as conn:
        row = conn.execute(
            "SELECT used_count FROM invite_codes WHERE code = ?", (code,)
        ).fetchone()
    return None if row is None else row["used_count"]


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
