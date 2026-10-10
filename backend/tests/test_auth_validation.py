"""认证链路的写入侧：注册、登录，以及它们背后的校验规则（工单 #51）。

本文件测的是**外部可观察的行为**：HTTP 状态码与响应体文案、哈希的加盐性质、
登录失败不泄露是哪个字段错了。不测私有函数，不断言内部调用顺序。

三处形状陷阱，改这块之前先读完：

1. ``validate_password`` 返回 ``str | None``（**文案或 None**），
   而 ``validate_email`` 返回 ``bool``。调用方因此写法不同：
   ``api_auth.py`` 对前者用 ``if err:``、对后者用 ``if not ...``。
   所以断言必须用 ``is None`` 与逐字 ``==``——写成
   ``assert not validate_password(...)`` 会把「返回 False 的另一种实现」一并放过。

2. ``if err:`` 是**真值判断**。「返回空字符串」与「返回 None」在路由层不可区分：
   空串让 ``err`` 为假，这个校验被静默跳过，400 不抛，注册直接成功。
   所以本文件对文案用逐字相等，不接受任何「非空即可」的写法。

3. 上下界是 ``> 50`` 与 ``< 6``，**不是** ``>=`` / ``<=``：6 位与 50 位都必须成功。
   off-by-one 不抛异常，只是恰好 50 位的用户莫名其妙注册不了，日志里什么都不留。

⚠️ **每一条碰库的用例都必须把 ``client_app`` 写进参数表。** 它会拉起 ``db`` 夹具，
它把 ``database.DB_PATH`` 换成本测试专属的临时库。没取夹具时 ``database.create_user``
落到的是**真实库** ``backend/data/app.db``——本文件第一版就犯过这个错，
往真实库里写进了两行用户。没有夹具的只有下面三个纯函数类，它们一行库都不碰。
"""
import pytest

import auth
import database
from seams import make_client


# ── 夹具 ──────────────────────────────────────────────────────


@pytest.fixture()
def client_app(db):
    """真实 app + 真实 client。

    刻意**不** ``with client`` —— 那会跑 lifespan → ``init_db()``，
    把 ``db`` 夹具刚建好的隔离库重置掉。接缝见 ``docs/TESTING-STRATEGY.md`` §6.4。
    """
    import main as main_module

    return make_client(main_module.app)


def _register(client, email, password):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _make_user_with_password(email, password):
    """建一个密码**真的**是 ``password`` 的用户，返回它的 id。

    不能用 ``make_user``：它写死哈希 ``"hash"``，bcrypt 验不过，
    于是任何登录用例都会在验证那一步就 400，测不到分支。

    ``create_user`` 返回的是**用户字典**不是 id（``api_auth.register`` 也在用
    ``user["id"]``），所以这里要自己取 id —— 直接把返回值当 id 用会撞上
    sqlite3.ProgrammingError。
    """
    return database.create_user(email, auth.hash_password(password))["id"]


# ── 校验规则本身（纯函数，不碰库，不取夹具）─────────────────


class TestPasswordRules:
    def test_returns_message_or_none_not_boolean(self):
        """返回形状是契约本身：文案或 None，**不是** 布尔。

        用 ``is None`` 而不是 ``not ...`` —— 后者会把「返回 False 的实现」
        也判成通过，而那会让调用方的 ``if err:`` 行为完全不同。
        """
        assert auth.validate_password("a" * 6) is None
        assert auth.validate_password("a" * 5) == "密码长度不能少于 6 位"
        assert auth.validate_password("a" * 50) is None
        assert auth.validate_password("a" * 51) == "密码长度不能超过 50 位"

    def test_boundary_is_inclusive_on_both_ends(self):
        """6 与 50 都必须**通过**，5 与 51 都必须被拒。

        off-by-one 往任一边偏，这四行里都有一行会红。单测 5 / 51 的话，
        把 ``> 50`` 改成 ``>= 50`` 时「51 被拒」那行照样绿。
        """
        for good in ("a" * 6, "a" * 50, "abc123"):
            assert auth.validate_password(good) is None, f"{len(good)} 位应当被接受"
        for bad in ("a" * 5, "a" * 51):
            assert auth.validate_password(bad) is not None


class TestEmailRules:
    def test_accepts_ordinary_addresses(self):
        for ok in ("u@example.com", "a.b+tag@sub.example.co.uk", "A_1%x@mail-host.org"):
            assert auth.validate_email(ok) is True, ok

    def test_rejects_addresses_that_would_break_the_route(self):
        for bad in ("", "no-at-sign", "@example.com", "u@", "u@example", "u @example.com"):
            assert auth.validate_email(bad) is False, bad


class TestPasswordHashing:
    def test_same_password_hashes_differently_both_verify(self):
        """bcrypt 随机盐：同一密码两次哈希必须不同，且都能验过。

        没有这条，「哈希」退化成明文或固定摘要都不会有人发现 ——
        而那种泄漏在库被读走之后才现形。
        """
        a = auth.hash_password("correct horse")
        b = auth.hash_password("correct horse")
        assert a != b, "同一密码两次哈希相同 = 没有随机盐"
        assert auth.verify_password("correct horse", a) is True
        assert auth.verify_password("correct horse", b) is True

    def test_wrong_password_does_not_verify(self):
        h = auth.hash_password("correct horse")
        assert auth.verify_password("correct horst", h) is False


# ── 注册路由（活端点：前端 src/api/auth.js 真在调）────────────


class TestRegister:
    def test_short_password_rejected_with_the_validators_own_words(self, client_app):
        r = _register(client_app, "new@example.com", "abc")
        assert r.status_code == 400
        assert r.json()["detail"] == "密码长度不能少于 6 位"

    def test_long_password_rejected_with_the_validators_own_words(self, client_app):
        r = _register(client_app, "new@example.com", "a" * 51)
        assert r.status_code == 400
        assert r.json()["detail"] == "密码长度不能超过 50 位"

    def test_boundary_passwords_are_accepted(self, client_app):
        """反向对照：上面两条「必须拒绝」的反面。

        缺了它，一个「一律返回 400」的实现能让上面两条全绿。
        两个边界都要，off-by-one 往任一边偏都有这一行挡着。
        """
        assert _register(client_app, "six@example.com", "a" * 6).status_code == 200
        assert _register(client_app, "fifty@example.com", "a" * 50).status_code == 200

    def test_accepted_registration_returns_a_token_for_that_user(self, client_app):
        r = _register(client_app, "fresh@example.com", "abc123")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["user"]["email"] == "fresh@example.com"
        # 发出去的 token 必须真的能换成这个用户 —— 只断言「有 token 字段」
        # 的话，一个恒返回字符串的实现照样绿。
        me = client_app.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {body['data']['token']}"},
        )
        assert me.status_code == 200
        assert me.json()["data"]["email"] == "fresh@example.com"

    def test_malformed_email_rejected(self, client_app):
        r = _register(client_app, "not-an-email", "abc123")
        assert r.status_code == 400
        assert r.json()["detail"] == "邮箱格式不正确"

    def test_wellformed_email_accepted(self, client_app):
        """反向对照：非法邮箱那条的反面。"""
        assert _register(client_app, "well.formed+tag@example.co.uk", "abc123").status_code == 200

    def test_duplicate_email_rejected(self, client_app):
        assert _register(client_app, "dup@example.com", "abc123").status_code == 200
        again = _register(client_app, "dup@example.com", "abc123")
        assert again.status_code == 400
        assert again.json()["detail"] == "该邮箱已注册"

    def test_another_email_still_accepted(self, client_app):
        """反向对照：重复邮箱那条的反面。"""
        assert _register(client_app, "one@example.com", "abc123").status_code == 200
        assert _register(client_app, "two@example.com", "abc123").status_code == 200

    def test_registered_password_can_immediately_log_in(self, client_app):
        """注册用的就是那串密码，登录必须过 —— 钉住写入侧与读取侧接得上。"""
        assert _register(client_app, "round@example.com", "abc123").status_code == 200
        assert _login(client_app, "round@example.com", "abc123").status_code == 200


# ── 登录路由 ──────────────────────────────────────────────────


class TestLogin:
    def test_does_not_leak_which_field_was_wrong(self, client_app):
        """邮箱不存在与密码错误，返回**同一句**。

        分成两句话就等于告诉攻击者「这个邮箱注册过」——枚举账号的入口。
        """
        _make_user_with_password("real@example.com", "abc123")
        unknown = _login(client_app, "nobody@example.com", "abc123")
        wrong = _login(client_app, "real@example.com", "wrongpw")
        assert unknown.status_code == 400
        assert wrong.status_code == 400
        assert unknown.json()["detail"] == wrong.json()["detail"] == "邮箱或密码错误"

    def test_correct_password_logs_in(self, client_app):
        """反向对照：上面那条「必须失败」的反面。"""
        _make_user_with_password("real@example.com", "abc123")
        r = _login(client_app, "real@example.com", "abc123")
        assert r.status_code == 200
        assert r.json()["data"]["user"]["email"] == "real@example.com"

    def test_login_response_carries_the_admin_flag(self, client_app):
        """``is_admin`` 必须来自库里那一列，且是布尔。

        前端没有别的途径知道「当前这个人是不是管理员」，少给这把钥匙时
        入口永远不显示，而症状看着像渲染 bug（工单 #11 的形状）。
        """
        uid = _make_user_with_password("plain@example.com", "abc123")
        r = _login(client_app, "plain@example.com", "abc123")
        assert r.json()["data"]["user"]["is_admin"] is False

        with database.get_db() as c:
            c.execute("UPDATE users SET is_admin=1 WHERE id=?", (uid,))
        promoted = _login(client_app, "plain@example.com", "abc123")
        assert promoted.json()["data"]["user"]["is_admin"] is True