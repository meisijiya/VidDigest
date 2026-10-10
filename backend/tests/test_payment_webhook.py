"""支付回调与订单列表（工单 #54）。

``POST /api/payment/webhook`` 与 ``GET /api/payment/orders`` 此前**零覆盖**，
而它们承载着「钱到不到账」这条线上唯一的写入口。

三个必须先知道的事实：

1. **幂等不在路由层。** ``stripe_webhook`` 无条件调 ``complete_order`` 并丢弃返回值，
   幂等由 ``database.complete_order`` 里那句 ``status = 'pending'`` 过滤实现。
   → 所以测试断言**效果**（VIP 到期时间），不断言 ``complete_order`` 被调了几次。
   断言调用次数是绑内部实现，且在幂等失效时**照样绿**。
2. **两种 400 理由不同，测试必须能区分。** 签名无效 → ``Invalid signature``；
   ``STRIPE_WEBHOOK_SECRET`` 未配置 → ``Webhook secret not configured``。
   都返回 400，但后者根本没走到验签。
3. ``stripe.SignatureVerificationError`` **不继承 ValueError**（MRO 是
   ``SignatureVerificationError → StripeError → Exception``），
   所以路由里 ``except ValueError`` 在前不会吞掉它——但这也意味着
   验签失败与「payload 不是 JSON」走的是**两个不同的分支**，各自要断。

签名用的是 ``stripe.WebhookSignature.generate_signature_header``，
纯离线 HMAC，不出网（门禁不得引入真实第三方依赖，见 §7）。
"""
import json

import pytest
import stripe

import auth
import database
from seams import auth_headers, make_client

WEBHOOK_URL = "/api/payment/webhook"
ORDERS_URL = "/api/payment/orders"
SECRET = "whsec_unit_test_secret"
SESSION_ID = "cs_test_1234567890"


@pytest.fixture()
def client_app(db):
    """真实 app + 真实 client。不 ``with client``（会跑 lifespan 重置 db 夹具）。"""
    import main as main_module

    return make_client(main_module.app)


@pytest.fixture()
def stripe_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_stub")
    return SECRET


def _pending_order(uid, session_id=SESSION_ID, plan_type="monthly", order_no="SA-TEST-1"):
    database.create_order(uid, order_no, 1990, "cny", plan_type)
    database.update_order_stripe_session(order_no, session_id)
    return order_no


def _event(session_id=SESSION_ID, payment_status="paid", event_id="evt_1",
           event_type="checkout.session.completed"):
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": {
            "id": session_id,
            "payment_status": payment_status,
            "payment_intent": "pi_test_1",
        }},
    }


def _post_webhook(client, event, secret=SECRET, sign=True):
    body = json.dumps(event).encode("utf-8")
    header = "t=1,v1=" + "0" * 64
    if sign:
        header = stripe.WebhookSignature.generate_signature_header(
            body.decode("utf-8"), secret)
    return client.post(WEBHOOK_URL, content=body,
                       headers={"stripe-signature": header})


def _vip_expire(uid):
    with database.get_db() as c:
        row = c.execute("SELECT vip_expire_at FROM users WHERE id=?", (uid,)).fetchone()
    return row["vip_expire_at"]


def _order_row(session_id=SESSION_ID):
    with database.get_db() as c:
        return c.execute(
            "SELECT * FROM orders WHERE stripe_session_id = ?", (session_id,)
        ).fetchone()


# ── 两种 400，理由必须可区分 ────────────────────────────────


class TestWebhookRejections:
    def test_invalid_signature_is_rejected(self, client_app, stripe_secret, make_user):
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event(), sign=False)
        assert r.status_code == 400
        assert r.json() == {"error": "Invalid signature"}
        assert _order_row()["status"] == "pending", \
            "验签没过就把订单标成已付 = 任何人伪造一个回调就能开通会员"
        assert _vip_expire(uid) is None, "验签没过却把用户标成了 VIP"

    def test_missing_secret_is_a_different_400(self, client_app, make_user, monkeypatch):
        """未配 secret 时**根本没走到验签**——两者的区别就在这里。

        少了这条，一个「所有失败都回同一句」的实现照样绿，
        而运维看到那句话会以为问题在签名算法上（真实原因是没配环境变量）。
        """
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event())
        assert r.status_code == 400
        assert r.json() == {"error": "Webhook secret not configured"}
        assert _order_row()["status"] == "pending"

    def test_signature_computed_with_another_secret_is_rejected(
        self, client_app, stripe_secret, make_user
    ):
        """用别的 secret 签出来的签名必须无效——这是「验签真的在验」的判据。"""
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event(), secret="whsec_a_different_secret")
        assert r.status_code == 400
        assert r.json() == {"error": "Invalid signature"}


# ── 正常回调 ──────────────────────────────────────────────────


class TestWebhookCompletesOrder:
    def test_paid_session_activates_membership(self, client_app, stripe_secret, make_user):
        uid = make_user(email="payer@example.com")
        _pending_order(uid, plan_type="monthly")

        r = _post_webhook(client_app, _event())
        assert r.status_code == 200
        assert r.json() == {"received": True}

        row = _order_row()
        assert row["status"] == "paid"
        assert row["stripe_payment_intent_id"] == "pi_test_1", \
            "支付流水号没写回订单——对账时无从下手"
        assert _vip_expire(uid) is not None, "订单付了却没开通会员"

    def test_unpaid_session_leaves_the_order_pending(self, client_app, stripe_secret, make_user):
        """``payment_status != paid`` 时**不得**开通。

        反向对照：上面那条走通了，这一条守的是「没付钱不许开通」。
        """
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event(payment_status="unpaid"))
        assert r.status_code == 200
        assert _order_row()["status"] == "pending"
        assert _vip_expire(uid) is None

    def test_async_payment_succeeded_also_completes(self, client_app, stripe_secret, make_user):
        """异步支付成功走另一条分支，必须同样开通。"""
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event(
            event_type="checkout.session.async_payment_succeeded"))
        assert r.status_code == 200
        assert _order_row()["status"] == "paid"
        assert _vip_expire(uid) is not None

    def test_unknown_event_type_is_acknowledged_without_touching_the_order(
        self, client_app, stripe_secret, make_user
    ):
        """不认识的事件类型回 200 但不动订单——Stripe 会因为非 200 一直重投。"""
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        r = _post_webhook(client_app, _event(event_type="customer.created"))
        assert r.status_code == 200
        assert _order_row()["status"] == "pending"


# ── 幂等 ──────────────────────────────────────────────────────


class TestWebhookIsIdempotent:
    def test_replay_does_not_extend_membership_a_second_time(
        self, client_app, stripe_secret, make_user
    ):
        r"""重放同一个事件只生效一次。

        判别力的构造：先手动把 \`vip_expire_at\` 推到 2030-01-01，再重放。
        · 幂等生效 → 订单已是 paid，被 \`status='pending'\` 挡掉，过期时间**不动**。
        · 幂等失效 → \`base_time = max(current_expire, now)\` 取 2030-01-01，
          再加一个月变成 2030-02-01 —— **被看见**。

        为什么不用「连发两次看时间没变」：同一瞬间发两次时，
        加一个月算出来是同一个值，两种实现结果一样，**变异存活**。
        """
        uid = make_user(email="payer@example.com")
        _pending_order(uid)
        assert _post_webhook(client_app, _event()).status_code == 200
        first_expiry = _vip_expire(uid)
        assert first_expiry is not None

        with database.get_db() as c:
            c.execute("UPDATE users SET vip_expire_at=? WHERE id=?",
                      ("2030-01-01T00:00:00+00:00", uid))
        seeded = _vip_expire(uid)

        assert _post_webhook(client_app, _event()).status_code == 200
        assert _vip_expire(uid) == seeded, \
            "重放把会员时长又加了一次——同一笔钱买了两次"

    def test_second_distinct_order_does_extend(
        self, client_app, stripe_secret, make_user
    ):
        """反向对照：一张**新**订单确实该再延一次。

        少了这条，「complete_order 整个变成空操作」也能让上一条绿。
        """
        uid = make_user(email="payer@example.com")
        _pending_order(uid, session_id="cs_A", order_no="SA-A")
        _pending_order(uid, session_id="cs_B", order_no="SA-B")
        assert _post_webhook(client_app, _event(session_id="cs_A")).status_code == 200
        after_first = _vip_expire(uid)
        assert _post_webhook(client_app, _event(
            session_id="cs_B", event_id="evt_2")).status_code == 200
        after_second = _vip_expire(uid)
        assert after_second > after_first, \
            "第二张订单付了却没延长会员——幂等做过头了"


# ── GET /api/payment/orders ───────────────────────────────────


class TestListOrders:
    def _auth(self, uid, email):
        return auth_headers(auth.create_token(uid, email))

    def test_returns_only_the_callers_own_orders(self, client_app, make_user):
        """隔离：用户 B 读不到 A 的订单。

        判断标准（§6.1）：**订单列表整体坏掉时它还会绿吗**——不会，
        所以这条不是「功能缺失型假通过」，而是真的断住了隔离这条边。
        """
        alice = make_user(email="alice@example.com")
        bob = make_user(email="bob@example.com")
        database.create_order(alice, "SA-ALICE", 1990, "cny", "monthly")
        database.create_order(bob, "SA-BOB", 1990, "cny", "monthly")

        r = client_app.get(ORDERS_URL, headers=self._auth(bob, "bob@example.com"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert [o["order_no"] for o in data] == ["SA-BOB"], \
            f"B 读到了别人的订单：{data}"

    def test_requires_login(self, client_app):
        assert client_app.get(ORDERS_URL).status_code == 401

    def test_empty_list_when_there_are_no_orders(self, client_app, make_user):
        """反向对照：真的没有订单时回空列表，不是 404 也不是 None。"""
        uid = make_user(email="fresh@example.com")
        r = client_app.get(ORDERS_URL, headers=self._auth(uid, "fresh@example.com"))
        assert r.status_code == 200
        assert r.json() == {"success": True, "data": []}