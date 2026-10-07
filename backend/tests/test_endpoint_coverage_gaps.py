"""三个「前端在用、后端零覆盖」的端点（工单 #42）。

## 为什么要专门开这个文件

对全仓 35 个路由做了一次「端点 × 调用方」矩阵扫描，结论是 0 个死代码、
0 个孤儿端点——但有 3 个**前端真实在调、后端一条测试都没有**的端点。

这个类别最危险的地方在于**它是双向看不见的**：`GET /api/history` 有覆盖，
它读的正是 `POST /api/history/save` 写进去的那些行。写入侧改坏了，读侧的
测试照样绿（它只断言自己喂进去的数据）；读侧改坏了，写侧的测试也不响。
两端各自有测试，中间那条线没人走过。

## 三条规矩

1. **走真实 HTTP 层**（`seams.make_client`），不打路由函数本身。
   直接调 `save_history(req, user)` 时 `user` 是我手写的 dict，鉴权一行都没执行。
2. **写侧的断言读回库里的行**，不只看返回的 `id`。
   返回值对不代表落库对——`upsert_parse_history` 有 7 个入参，
   少传一个照样返回 200 和一个正确的 id。
3. **支付用桩替掉 `stripe`**，绝不真的出网。桩要能被观测：
   `calls` 记次数，`explode` 让它抛，用来证明失败路径真的被走过。

## 「零覆盖」这个前提本身

工单里写了：这套 grep 在已知有覆盖的路径（`/api/history/facets`、
`/api/community/search`、`/api/auth/me`）上命中 47 处 / 5 个文件，
所以「这三个 0 命中」是可信的，不是工具没跑。
"""
import pytest

import api_payment
import auth
import database
from seams import auth_headers, make_client


# ── 夹具 ──────────────────────────────────────────────────────


@pytest.fixture()
def app(db):
    """真实 app + 真实 client。刻意不 `with client`（见文件头第 1 条）。"""
    import main as main_module

    return make_client(main_module.app)


@pytest.fixture()
def user(app, make_user):
    uid = make_user(email="u@example.com")
    return uid, auth_headers(auth.create_token(uid, "u@example.com"))


def _history_row(video_url):
    with database.get_db() as c:
        return c.execute(
            "SELECT * FROM parse_history WHERE video_url = ?", (video_url,)
        ).fetchone()


def _history_urls():
    with database.get_db() as c:
        return [r["video_url"] for r in c.execute("SELECT video_url FROM parse_history")]


def _ready_video(url, tags):
    database.reserve_video(url, None)
    database.complete_video(url, summary_md="总结正文", tags=tags)


# ── POST /api/history/save ────────────────────────────────────


class TestSaveHistory:
    URL = "/api/history/save"

    def test_seven_fields_all_reach_the_row(self, app, user):
        """7 个入参逐个读回来核对——返回值对不代表落库对。"""
        _uid, hdr = user
        body = {
            "url": "https://v.example/a",
            "video_title": "标题甲",
            "video_data": {"duration": 42, "uploader": "乙"},
            "summary_md": "# 总结丙",
            "mindmap_md": "# 导图丁",
            "subtitle_data": {"has_subtitle": True, "full_text": "字幕戊"},
        }
        r = app.post(self.URL, json=body, headers=hdr)
        assert r.status_code == 200, r.text

        row = _history_row(body["url"])
        assert row is not None, "接口返回了成功，但库里没有这一行"
        assert row["video_title"] == "标题甲", "video_title 没落库"
        assert row["summary_md"] == "# 总结丙", "summary_md 没落库"
        assert row["mindmap_md"] == "# 导图丁", "mindmap_md 没落库"
        # 三个 JSON 列以文本存，读回要能解析——解析不了就是写成了别的形态
        import json

        assert json.loads(row["video_data"]) == {"duration": 42, "uploader": "乙"}, \
            f"video_data 落库后不是原来的形状：{row['video_data']!r}"
        assert json.loads(row["subtitle_data"])["full_text"] == "字幕戊", \
            f"subtitle_data 落库后不是原来的形状：{row['subtitle_data']!r}"

    def test_empty_field_does_not_wipe_a_previous_value(self, app, user):
        """`upsert_parse_history` 的合并语义：空值保留旧值，非空才覆盖。

        这条是「视频源保存不覆盖 AI 结果」那句话的唯一见证。改成无条件覆盖，
        症状是：用户在总结页点一下保存，历史里的总结就没了。
        """
        _uid, hdr = user
        url = "https://v.example/b"
        app.post(self.URL, json={"url": url, "video_title": "标题", "summary_md": "# 旧总结"},
                 headers=hdr)
        # 第二次只带标题（模拟「打开页面就自动存一下」）
        r = app.post(self.URL, json={"url": url, "video_title": "新标题"}, headers=hdr)
        assert r.status_code == 200, r.text

        row = _history_row(url)
        assert row["video_title"] == "新标题", "非空新值必须覆盖旧值"
        assert row["summary_md"] == "# 旧总结", \
            "空的 summary_md 把旧总结抹掉了 —— 合并语义反了"

    def test_saving_the_same_url_twice_does_not_insert_a_second_row(self, app, user):
        _uid, hdr = user
        url = "https://v.example/c"
        first = app.post(self.URL, json={"url": url, "summary_md": "# 一"}, headers=hdr)
        second = app.post(self.URL, json={"url": url, "summary_md": "# 二"}, headers=hdr)
        assert first.status_code == 200 and second.status_code == 200
        assert _history_urls().count(url) == 1, \
            f"同一视频存了两次，库里出现了多行：{_history_urls()}"
        assert first.json()["id"] == second.json()["id"], \
            "两次保存返回了不同的 id，说明插了第二行而不是更新"
        assert _history_row(url)["summary_md"] == "# 二"

    def test_requires_login(self, app, db, make_user):
        """这个端点唯一的边界。返回 200 就是鉴权没被执行。

        刻意 `make_user(...)` 先把一个**真实用户**放进库里：否则「没鉴权」
        与「库里没这个人」会混成同一件事——请求带着不存在的 user 去写库，
        FK 违约抛 IntegrityError，测试以异常结束而不是断言失败，
        判别力就打折了。库里有用户之后，去掉鉴权的唯一后果就是**写成功**。
        """
        make_user(email="real@example.com")
        r = app.post(self.URL, json={"url": "https://v.example/d"})
        assert r.status_code == 401, (
            f"未登录保存历史应当 401，实得 {r.status_code} —— 鉴权依赖没被执行")
        assert _history_urls() == [], "未登录竟然写进去了"

    def test_one_user_cannot_overwrite_another_users_row(self, app, db, user, make_user):
        """A 的保存不能改到 B 的行上——这是按 (user_id, video_url) 去重的含义。

        显式取 `db`：下面直接用 `database.get_db()` 读库。经 `app` / `user`
        间接拿到同一个夹具也能跑，但 `test_db_fixture_guard` 要求**这个用例
        自己**看得见隔离是刻意的——否则读者只能推断。
        """
        _uid_a, hdr_a = user
        uid_b = make_user(email="b@example.com")
        url = "https://v.example/e"
        app.post(self.URL, json={"url": url, "summary_md": "# A 的"}, headers=hdr_a)
        app.post(self.URL, json={"url": url, "summary_md": "# B 的"},
                 headers=auth_headers(auth.create_token(uid_b, "b@example.com")))

        with database.get_db() as c:
            rows = c.execute(
                "SELECT user_id, summary_md FROM parse_history WHERE video_url = ?",
                (url,)).fetchall()
        assert len(rows) == 2, f"两个人的同一视频应当是两行，实得 {len(rows)} 行"
        assert {r["user_id"] for r in rows} == {_uid_a, uid_b}
        assert sorted(r["summary_md"] for r in rows) == ["# A 的", "# B 的"]


# ── GET /api/community/tags ──────────────────────────────────


class TestCommunityTags:
    URL = "/api/community/tags"

    def test_items_are_derived_from_the_database_not_a_separate_copy(self, app, db):
        """端点返回的标签与条数必须等于库里真的有的那些。

        「看起来一致」不是发现——所以这里不写死某个标签列表，而是先造数据、
        再断言端点报出来的**恰好**是这些数据。
        """
        _ready_video("https://v.example/t1", ["编程", "读书"])
        _ready_video("https://v.example/t2", ["编程"])
        _ready_video("https://v.example/t3", ["健身"])

        r = app.get(self.URL)
        assert r.status_code == 200, r.text
        got = {i["tag"]: i["count"] for i in r.json()["items"]}
        assert got == {"编程": 2, "读书": 1, "健身": 1}, \
            f"标签与条数与库里不一致：{got}"

    def test_pending_rows_are_not_counted(self, app, db):
        """`list_community_tags` 的 docstring 说「只数 ready 行」——这句话要有东西守。"""
        _ready_video("https://v.example/p1", ["编程"])
        database.reserve_video("https://v.example/p2", None)  # 只占位，没写回
        with database.get_db() as c:
            c.execute("UPDATE videos SET tags = '[\"幽灵标签\"]' WHERE status != 'ready'")

        r = app.get(self.URL)
        assert r.status_code == 200, r.text
        tags = {i["tag"] for i in r.json()["items"]}
        assert tags == {"编程"}, f"未就绪的行被算进标签行了：{tags}"

    def test_readable_without_login(self, app, db):
        """docstring 明写「**未登录也可用**」，而这句话当前没有任何东西守着。"""
        _ready_video("https://v.example/guest", ["科普"])
        r = app.get(self.URL)          # 不带 Authorization
        assert r.status_code == 200, \
            f"未登录读标签应当 200，实得 {r.status_code} —— docstring 的声称不成立"
        assert {i["tag"] for i in r.json()["items"]} == {"科普"}

    def test_item_shape(self, app, db):
        """键集合恰好相等：多一个键是泄漏，少一个键是界面拿不到东西。"""
        _ready_video("https://v.example/s1", ["编程"])
        items = app.get(self.URL).json()["items"]
        assert items, "造了数据却一个标签都没返回"
        for it in items:
            assert set(it) == {"tag", "count"}, \
                f"标签项的键集合应当恰好是 {{tag, count}}，实得 {sorted(it)}"


# ── POST /api/payment/create-checkout ────────────────────────


class _StubStripeError(Exception):
    """桩自己抛的错，用来驱动 `except stripe.StripeError` 分支。"""


class _StubSession:
    def __init__(self):
        self.id = "cs_stub_123"
        self.url = "https://checkout.stripe.test/cs_stub_123"


class _StubStripe:
    """替掉整个 `stripe` 模块。`calls` 记次数，`explode` 决定它抛不抛。"""

    def __init__(self, explode=False):
        self.api_key = None
        self.calls = 0
        self.explode = explode
        self.StripeError = _StubStripeError
        session = _StubSession()
        outer = self

        class _Checkout:
            class Session:
                @staticmethod
                def create(**kwargs):
                    outer.calls += 1
                    if outer.explode:
                        raise _StubStripeError("stub failure")
                    session.kwargs = kwargs
                    return session

        self.checkout = _Checkout


@pytest.fixture()
def stripe_env(monkeypatch):
    """把 Stripe 三个环境变量设成可用值，并替掉 stripe 客户端。"""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_stub")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_stub")
    monkeypatch.setenv("FRONTEND_URL", "https://app.test")
    stub = _StubStripe()
    monkeypatch.setattr(api_payment, "stripe", stub)
    return stub


class TestCreateCheckout:
    URL = "/api/payment/create-checkout"

    def test_requires_login(self, app, db, stripe_env):
        r = app.post(self.URL, json={"plan_type": "monthly"})
        assert r.status_code == 401, \
            f"未登录下单应当 401，实得 {r.status_code}"
        assert stripe_env.calls == 0, "鉴权没过就已经去调 Stripe 了"
        with database.get_db() as c:
            assert c.execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 0, \
                "未登录竟然写进去了订单"

    def test_missing_stripe_config_is_500(self, app, user, monkeypatch):
        """没配 key 时必须 500 而不是带着 None 去调 Stripe。

        顺序很重要：这条必须在**清掉环境变量**之后、且**没有替掉 stripe** 时跑，
        否则「500」可能来自桩而不是来自那段配置检查。
        """
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        _uid, hdr = user
        r = app.post(self.URL, json={"plan_type": "monthly"}, headers=hdr)
        assert r.status_code == 500, (
            f"缺 STRIPE_SECRET_KEY 应当 500（明确告诉运维没配），实得 {r.status_code}：{r.text}")

    def test_invalid_plan_type_is_400(self, app, user, stripe_env):
        _uid, hdr = user
        r = app.post(self.URL, json={"plan_type": "yearly"}, headers=hdr)
        assert r.status_code == 400, (
            f"未知套餐应当 400，实得 {r.status_code} —— 会被当成月度套餐卖掉")
        assert stripe_env.calls == 0, "参数都非法了还去调了 Stripe"

    def test_success_returns_url_order_no_and_session_id(self, app, db, user, stripe_env):
        """显式取 `db`：下面要读 `orders` 表核对订单行（见守卫的约定）。"""
        _uid, hdr = user
        r = app.post(self.URL, json={"plan_type": "monthly"}, headers=hdr)
        assert r.status_code == 200, r.text

        data = r.json()["data"]
        assert set(data) == {"checkout_url", "order_no", "session_id"}, \
            f"返回的键集合应当恰好是三个，实得 {sorted(data)}"
        assert data["checkout_url"], "checkout_url 是空的，前端拿不到跳转地址"
        assert data["order_no"].startswith("SA"), \
            f"order_no 形状不对：{data['order_no']!r}"
        assert stripe_env.calls == 1, \
            f"应当恰好调 Stripe 一次，实得 {stripe_env.calls} 次"

        # 订单号必须真的落库，且带着用户与金额——否则 webhook 回来对不上
        with database.get_db() as c:
            row = c.execute(
                "SELECT * FROM orders WHERE order_no = ?",
                (data["order_no"],)).fetchone()
        assert row is not None, f"订单号 {data['order_no']} 没落库"
        assert row["user_id"] == _uid
        assert row["status"] == "pending", \
            f"刚建的订单状态应当是 pending，实得 {row['status']}"
        assert row["stripe_session_id"] == data["session_id"], \
            "session id 没写回订单行 —— webhook 拿它对不上账"

    def test_stripe_failure_surfaces_as_400(self, app, user, monkeypatch):
        """Stripe 抛错时必须变成 400，不是 500 更不是静默成功。

        记录在案、**本单不修**：本地订单在调 Stripe 之前就写了，
        失败后没人清理，`order_no` 每次唯一所以那行会一直挂着。
        那是行为改动且属支付边界，不在「补覆盖」这一单里。
        """
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_stub")
        monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_stub")
        monkeypatch.setenv("FRONTEND_URL", "https://app.test")
        monkeypatch.setattr(api_payment, "stripe", _StubStripe(explode=True))
        _uid, hdr = user

        r = app.post(self.URL, json={"plan_type": "monthly"}, headers=hdr)
        assert r.status_code == 400, (
            f"Stripe 失败应当 400，实得 {r.status_code}：{r.text}")
        assert "创建支付会话失败" in r.json()["detail"], \
            "400 的 detail 没告诉用户发生了什么"
