"""账号生命周期：建号 / 管理员标记 / 删号（ADR 0012）。

沿用 test_admin_api.py 的三条硬规矩：走真实 HTTP 层且不触发 lifespan、
安全断言必有正反双向对照、断言具体值而不是只断言 200。

删除语义是这份测试的主角：**有内容就 409，绝不静默级联**。
"""
import pytest

import auth
import database
from seams import auth_headers, make_client


@pytest.fixture()
def client_app(db, make_user):
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


def _chat(user_id: int, video_url: str = "https://v.example/x") -> None:
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (user_id, video_url, role, content) "
            "VALUES (?, ?, 'user', 'hi')", (user_id, video_url))


def _video(parsed_by: int, url: str = "https://v.example/ready") -> int:
    with database.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO videos (video_url, status, parsed_by, video_title) "
            "VALUES (?, 'ready', ?, 't')", (url, parsed_by))
        return cur.lastrowid


def _parse_history(user_id: int, url: str = "https://v.example/p") -> None:
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO parse_history (user_id, video_url) VALUES (?, ?)",
            (user_id, url))


# ── 数据层 ────────────────────────────────────────────────────

class TestCreate:
    def test_plain_user_has_no_quota_override(self, db, make_user):
        """新号一律回落全局上限。偷偷给一份初值会让「这个号为什么
        和别人不一样」变成查不到的历史。"""
        admin_id = make_user(email="a@example.com")
        created = database.create_admin_user("new@example.com", "h", False)
        row = database.get_user_by_id(created["id"])
        assert row["is_admin"] == 0
        assert row["parse_limit_override"] is None
        assert row["chat_limit_override"] is None
        assert admin_id != created["id"]

    def test_admin_flag_is_written(self, db):
        created = database.create_admin_user("boss2@example.com", "h", True)
        assert database.get_user_by_id(created["id"])["is_admin"] == 1


class TestSetAdmin:
    def test_promote_and_demote(self, db, make_user):
        uid = make_user(email="p@example.com")
        # 先有第二个管理员：只有一个管理员时降级会被
        # 「不能把最后一个管理员降级」挡下（下面那条专门测它）。
        other = make_user(email="other-admin@example.com")
        database.set_user_admin(other, True)

        assert database.set_user_admin(uid, True) == 1
        assert database.get_user_by_id(uid)["is_admin"] == 1
        assert database.set_user_admin(uid, False) == 1
        assert database.get_user_by_id(uid)["is_admin"] == 0

    def test_missing_user_returns_zero(self, db):
        assert database.set_user_admin(999999, True) == 0

    def test_cannot_demote_self(self, db, make_user):
        uid = make_user(email="self@example.com")
        with database.get_db() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (uid,))
        with pytest.raises(database.UserConflict):
            database.set_user_admin(uid, False, acting_id=uid)
        assert database.get_user_by_id(uid)["is_admin"] == 1, "拒绝后不该改到数据"

    def test_cannot_demote_the_last_admin(self, db, make_user):
        uid = make_user(email="last@example.com")
        with database.get_db() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (uid,))
        other = make_user(email="other@example.com")
        with pytest.raises(database.UserConflict):
            database.set_user_admin(uid, False, acting_id=other)
        assert database.get_user_by_id(uid)["is_admin"] == 1


class TestDelete:
    def test_empty_user_is_deleted(self, db, make_user):
        uid = make_user(email="gone@example.com")
        database.delete_user(uid)
        assert database.get_user_by_id(uid) is None

    def test_missing_user_is_a_noop(self, db):
        database.delete_user(999999)          # 幂等，不抛

    def test_parse_history_blocks_deletion(self, db, make_user):
        uid = make_user(email="busy@example.com")
        _parse_history(uid)
        with pytest.raises(database.UserConflict) as e:
            database.delete_user(uid)
        assert e.value.blockers == {"parse_history": 1}
        assert database.get_user_by_id(uid) is not None, "被拒后不该把人删掉"

    def test_order_blocks_deletion(self, db, make_user):
        uid = make_user(email="paid@example.com")
        database.create_order(uid, "ORD-1", 1990)
        with pytest.raises(database.UserConflict) as e:
            database.delete_user(uid)
        assert e.value.blockers == {"orders": 1}
        assert database.get_user_by_id(uid) is not None

    def test_counts_are_actual_numbers_not_booleans(self, db, make_user):
        """blockers 要给前端的是「还剩几行」，不是「有没有」。"""
        uid = make_user(email="many@example.com")
        _parse_history(uid, "https://v.example/1")
        _parse_history(uid, "https://v.example/2")
        _parse_history(uid, "https://v.example/3")
        with pytest.raises(database.UserConflict) as e:
            database.delete_user(uid)
        assert e.value.blockers["parse_history"] == 3

    def test_chat_messages_go_with_the_user(self, db, make_user):
        uid = make_user(email="chatty@example.com")
        _chat(uid)
        database.delete_user(uid)
        with database.get_db() as conn:
            left = conn.execute(
                "SELECT count(*) FROM chat_messages WHERE user_id = ?", (uid,)
            ).fetchone()[0]
        assert left == 0, "会话记录必须随用户消失，不能留孤儿行"

    def test_community_videos_survive(self, db, make_user):
        """videos.parsed_by 是弱引用：解析者注销后社区内容必须留下来。"""
        uid = make_user(email="author@example.com")
        vid = _video(uid)
        database.delete_user(uid)
        with database.get_db() as conn:
            row = conn.execute("SELECT parsed_by FROM videos WHERE id = ?", (vid,)).fetchone()
        assert row is not None, "社区视频被连带删了——这是 ADR 0010 明写不能发生的"

    def test_cannot_delete_self(self, db, make_user):
        """**必须先有第二个管理员**，否则这条测不出自删守卫。

        第一次写这条时场上只有一个管理员，于是「不能删自己」与
        「不能删最后一个管理员」两个守卫都会抛同样的异常——删掉其中
        一个测试照样绿。判据在两个原因之间不咬人，就等于没有判据。
        """
        uid = make_user(email="me@example.com")
        backup = make_user(email="backup@example.com")
        with database.get_db() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id IN (?, ?)", (uid, backup))
        with pytest.raises(database.UserConflict) as e:
            database.delete_user(uid, acting_id=uid)
        assert "自己" in str(e.value), f"应是自删守卫拦下的：{e.value}"
        assert database.get_user_by_id(uid) is not None

    def test_cannot_delete_the_last_admin(self, db, make_user):
        uid = make_user(email="solo@example.com")
        other = make_user(email="nobody@example.com")
        with database.get_db() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (uid,))
        with pytest.raises(database.UserConflict):
            database.delete_user(uid, acting_id=other)
        assert database.get_user_by_id(uid) is not None


# ── 端点层 ────────────────────────────────────────────────────

class TestCreateEndpoint:
    def test_creates_and_password_actually_works(self, client_app):
        client, _, _ = client_app
        r = client.post("/api/admin/users", json={
            "email": "fresh@example.com", "password": "s3cret-pass",
        }, headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        row = database.get_user_by_email("fresh@example.com")
        assert row is not None
        # 建号不是把明文塞进去：散列必须真的能验回来
        assert auth.verify_password("s3cret-pass", row["password_hash"])
        assert not auth.verify_password("wrong-pass", row["password_hash"])

    def test_response_is_a_read_back_not_an_echo(self, client_app):
        client, _, _ = client_app
        r = client.post("/api/admin/users", json={
            "email": "echo@example.com", "password": "s3cret-pass",
        }, headers=_admin_hdr(client_app))
        body = r.json()["user"]
        assert body["email"] == "echo@example.com"
        # 额度列必须是回落后的真实值，而不是「我们打算写什么」
        assert body["parse_limit_source"] == "global"

    def test_bad_email_and_short_password(self, client_app):
        client, _, _ = client_app
        h = _admin_hdr(client_app)
        bad = client.post("/api/admin/users",
                          json={"email": "not-an-email", "password": "s3cret-pass"},
                          headers=h)
        short = client.post("/api/admin/users",
                            json={"email": "ok@example.com", "password": "123"},
                            headers=h)
        assert bad.status_code == 400, bad.text
        assert short.status_code == 400, short.text
        assert database.get_user_by_email("not-an-email") is None
        assert database.get_user_by_email("ok@example.com") is None

    def test_duplicate_email(self, client_app):
        client, _, _ = client_app
        h = _admin_hdr(client_app)
        payload = {"email": "dupe@example.com", "password": "s3cret-pass"}
        first = client.post("/api/admin/users", json=payload, headers=h)
        second = client.post("/api/admin/users", json=payload, headers=h)
        assert first.status_code == 200, first.text
        assert second.status_code == 400, second.text

    def test_requires_admin(self, client_app):
        """正反对照：只断 403 的话「全员 403」也能让它绿。"""
        client, _, _ = client_app
        payload = {"email": "nope@example.com", "password": "s3cret-pass"}
        ok = client.post("/api/admin/users", json=payload, headers=_admin_hdr(client_app))
        no = client.post("/api/admin/users", json=payload, headers=_plain_hdr(client_app))
        anon = client.post("/api/admin/users", json=payload)
        assert ok.status_code == 200
        assert no.status_code == 403
        assert anon.status_code == 401

    def test_unknown_field_is_rejected(self, client_app):
        client, _, _ = client_app
        r = client.post("/api/admin/users", json={
            "email": "x@example.com", "password": "s3cret-pass", "is_vip": True,
        }, headers=_admin_hdr(client_app))
        assert r.status_code == 422, r.text
        assert database.get_user_by_email("x@example.com") is None


class TestSetAdminEndpoint:
    def test_promote_then_demote(self, client_app):
        client, _, plain_id = client_app
        h = _admin_hdr(client_app)
        up = client.patch(f"/api/admin/users/{plain_id}", json={"is_admin": True}, headers=h)
        assert up.status_code == 200, up.text
        # is_admin 回的是 0/1 而不是 true/false：_admin_user_item 原样透传，
        # 这与 GET /api/admin/users 列表**同一形状**（_build_user_response 里
        # 的注释解释过为什么 bool() 归一要发生在前端 toUser 那一层）。
        assert up.json()["user"]["is_admin"] == 1
        assert database.get_user_by_id(plain_id)["is_admin"] == 1
        down = client.patch(f"/api/admin/users/{plain_id}", json={"is_admin": False}, headers=h)
        assert down.json()["user"]["is_admin"] == 0

    def test_demote_self_is_409(self, client_app):
        client, admin_id, _ = client_app
        r = client.patch(f"/api/admin/users/{admin_id}",
                         json={"is_admin": False}, headers=_admin_hdr(client_app))
        assert r.status_code == 409, r.text
        assert database.get_user_by_id(admin_id)["is_admin"] == 1

    def test_unknown_user_is_404(self, client_app):
        client, _, _ = client_app
        r = client.patch("/api/admin/users/999999",
                         json={"is_admin": True}, headers=_admin_hdr(client_app))
        assert r.status_code == 404, r.text

    def test_no_field_is_400(self, client_app):
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}", json={},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text

    # ── is_admin 的值域：Any 声明必须配一个校验器，否则静默反向 ──
    #
    # UserAdminUpdateRequest 用 `Any` 是本文件约定（非法值走 400 而不是 422），
    # 代价是这个字段没有类型保证。少了 _as_admin_flag：
    #   {"is_admin": []}      -> bool([])  == False  静默撤权
    #   {"is_admin": {}}      -> bool({})  == False  静默撤权
    #   {"is_admin": "false"} -> bool(str) == True   **静默提权**（反向，最坏）
    @pytest.mark.parametrize("bad", [[], {}, "false", "true", 1, 0, 1.5])
    def test_non_bool_is_admin_is_400(self, client_app, bad):
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}",
                         json={"is_admin": bad}, headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        # 关键断言：不看状态码也必须成立——400 了却把权限改了等于没修。
        assert database.get_user_by_id(plain_id)["is_admin"] == 0, (
            f"发了 {bad!r} 之后权限竟然变了")

    def test_string_false_does_not_promote(self, client_app):
        """单拎出来：这是唯一一个「想撤权却变成提权」的输入。"""
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}",
                         json={"is_admin": "false"}, headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert database.get_user_by_id(plain_id)["is_admin"] == 0

    def test_explicit_null_is_400_not_a_flag(self, client_app):
        """null 不是「改成 false」，是「没说要改」——两者不能共用一条路径。"""
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}",
                         json={"is_admin": None}, headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert database.get_user_by_id(plain_id)["is_admin"] == 0

    def test_vip_is_not_writable_here(self, client_app):
        """VIP 不在后台可改范围（项目范围边界：会员判定留在代码里不动）。"""
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}",
                         json={"is_vip": True, "vip_expire_at": "2099-01-01"},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 422, r.text
        assert database.get_user_by_id(plain_id)["is_vip"] == 0

    def test_requires_admin(self, client_app):
        client, _, plain_id = client_app
        r = client.patch(f"/api/admin/users/{plain_id}",
                         json={"is_admin": True}, headers=_plain_hdr(client_app))
        assert r.status_code == 403, r.text
        assert database.get_user_by_id(plain_id)["is_admin"] == 0


class TestDeleteEndpoint:
    def test_deletes_and_really_gone(self, client_app, make_user):
        client, _, _ = client_app
        uid = make_user(email="temp@example.com")
        r = client.delete(f"/api/admin/users/{uid}", headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": uid}
        assert database.get_user_by_id(uid) is None

    def test_conflict_carries_blocker_counts(self, client_app, make_user):
        client, _, _ = client_app
        uid = make_user(email="busy@example.com")
        _parse_history(uid, "https://v.example/1")
        _parse_history(uid, "https://v.example/2")
        r = client.delete(f"/api/admin/users/{uid}", headers=_admin_hdr(client_app))
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["blockers"] == {"parse_history": 2}, body
        assert database.get_user_by_id(uid) is not None

    def test_delete_self_is_409(self, client_app):
        """同数据层那条：先提一个第二管理员，否则 409 可能来自
        「不能删最后一个管理员」而不是自删守卫，判据就不咬人了。"""
        client, admin_id, plain_id = client_app
        client.patch(f"/api/admin/users/{plain_id}",
                     json={"is_admin": True}, headers=_admin_hdr(client_app))
        r = client.delete(f"/api/admin/users/{admin_id}", headers=_admin_hdr(client_app))
        assert r.status_code == 409, r.text
        assert "自己" in r.json()["detail"], r.text
        assert database.get_user_by_id(admin_id) is not None

    def test_requires_admin(self, client_app, make_user):
        client, _, _ = client_app
        uid = make_user(email="keep@example.com")
        no = client.delete(f"/api/admin/users/{uid}", headers=_plain_hdr(client_app))
        anon = client.delete(f"/api/admin/users/{uid}")
        assert no.status_code == 403, no.text
        assert anon.status_code == 401, anon.text
        assert database.get_user_by_id(uid) is not None, "没鉴权也删掉了"


class TestNoCredentialsInResponses:
    """三条新端点的响应体都不许出现凭据形状的键（ADR 0004 / 0011）。"""

    HINTS = ("key", "token", "secret", "password", "credential")

    def _scan(self, payload, where):
        if isinstance(payload, dict):
            for k, v in payload.items():
                assert not any(h in k.lower() for h in self.HINTS), f"{where}: {k!r}"
                self._scan(v, where)
        elif isinstance(payload, list):
            for item in payload:
                self._scan(item, where)

    def test_all_three(self, client_app, make_user):
        client, _, _ = client_app
        h = _admin_hdr(client_app)
        made = client.post("/api/admin/users", json={
            "email": "scan@example.com", "password": "s3cret-pass",
        }, headers=h)
        self._scan(made.json(), "POST /admin/users")

        target = make_user(email="target@example.com")
        patched = client.patch(f"/api/admin/users/{target}",
                               json={"is_admin": True}, headers=h)
        self._scan(patched.json(), "PATCH /admin/users/{id}")

        gone = make_user(email="gone2@example.com")
        deleted = client.delete(f"/api/admin/users/{gone}", headers=h)
        self._scan(deleted.json(), "DELETE /admin/users/{id}")
