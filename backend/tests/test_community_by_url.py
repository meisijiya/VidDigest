"""GET /api/community/videos/by-url —— 社区里「有没有这一条、我能不能改」（ADR 0007）。

这个端点是被一次真机验证逼出来的：横幅上的「重新解析」按钮在
**别人**解析的视频上压根不出现。查下去发现前端判「社区里有没有」
用的是个人解析历史——而陌生人打开一条社区视频时，他自己的历史里
当然没有这一条，于是必然判成「没有」。

所以这里守的不是「端点能不能返回 200」，而是**判据那张表**：
同一个链接，owner 问与外人问必须给出不同的答案，
而「社区里根本没有」必须与「正在解析中」也区分开。

走真实 HTTP 层（seams.make_client），因为路由顺序与鉴权都在这一层：
本文件的 by-url 端点必须声明在 /videos/{video_id} 之前，
否则 "by-url" 会被 int 路径参数吃掉、恒返回 422——
那种失败在直接调函数时根本看不见。
"""
import pytest

import auth
import database
from seams import auth_headers, make_client
from test_community_api import seed_community_video

URL = "https://www.bilibili.com/video/BV1byUrlCheck"


@pytest.fixture()
def client_app(db, make_user):
    import main as main_module
    return main_module.app, make_user


def as_user(app, uid, email="u@example.com"):
    return make_client(app), auth_headers(auth.create_token(uid, email))


def probe(app, uid, url=URL, email="u@example.com"):
    client, headers = as_user(app, uid, email)
    return client.get("/api/community/videos/by-url", params={"url": url}, headers=headers)


class TestEndpointIsReachableAtAll:
    def test_it_is_not_swallowed_by_the_int_path_parameter(self, client_app, db):
        """路由顺序：by-url 必须排在 /videos/{video_id} 前面。

        判据是 200 而不是「不是 422」：一个被 {video_id} 吃掉的路径
        同样不是 422 之外的错误，但它返回的是详情端点的 404，
        用 200 断言才不会把这两种情况混为一谈。
        """
        app, _mk = client_app
        seed_community_video(URL)
        uid = _mk()
        r = probe(app, uid)
        assert r.status_code == 200, f"被路由吞掉了：{r.status_code} {r.text[:200]}"

    def test_anonymous_is_401_not_an_empty_answer(self, client_app, db):
        """未登录必须 401。

        判据不能是「拿不到内容」——返回 exists:false 也是一个合法的
        答案形状，访客会以为社区里真的没有这条视频。
        """
        app, _mk = client_app
        seed_community_video(URL)
        with make_client(app) as c:
            r = c.get("/api/community/videos/by-url", params={"url": URL})
        assert r.status_code == 401, r.text

    def test_missing_url_param_is_422(self, client_app, db):
        app, _mk = client_app
        uid = _mk()
        client, headers = as_user(app, uid)
        r = client.get("/api/community/videos/by-url", headers=headers)
        assert r.status_code == 422, r.text


class TestExistenceIsDecidedByTheCommunityTable:
    def test_owner_sees_it_exists_and_may_regenerate(self, client_app, db):
        app, _mk = client_app
        owner = _mk("owner@example.com")
        seed_community_video(URL, user_id=owner)

        r = probe(app, owner, email="owner@example.com")

        assert r.status_code == 200
        assert r.json() == {"exists": True, "can_regenerate": True}

    def test_stranger_sees_it_exists_but_may_not_regenerate(self, client_app, db):
        """**这一条是整个端点的存在理由。**

        陌生人打开社区视频时自己的解析历史里没有这一条。
        如果这里返回 exists:false，他就不会看到复用提示，
        「重新解析」按钮也就不会出现——功能对他是彻底不存在的。
        """
        app, _mk = client_app
        owner = _mk("owner@example.com")
        seed_community_video(URL, user_id=owner)
        stranger = _mk("stranger@example.com")

        r = probe(app, stranger, email="stranger@example.com")

        assert r.json() == {"exists": True, "can_regenerate": False}, (
            "别人解析过的视频在陌生人眼里不存在了"
        )

    def test_unknown_url_does_not_exist(self, client_app, db):
        app, _mk = client_app
        uid = _mk()
        r = probe(app, uid, url="https://www.bilibili.com/video/BVneverParsed")
        assert r.json() == {"exists": False, "can_regenerate": False}

    def test_a_placeholder_is_not_a_result(self, client_app, db):
        """别人正在解析时那行是 pending：不是内容，不能报 exists。"""
        app, _mk = client_app
        uid = _mk()
        database.reserve_video(URL, uid)

        r = probe(app, uid)

        assert r.json() == {"exists": False, "can_regenerate": False}, (
            "把别人正在解析的空壳当成了社区内容"
        )

    def test_a_null_owned_row_belongs_to_nobody(self, client_app, db):
        """parsed_by 为 NULL 的行：谁都改不了，但内容是公开可复用的。"""
        app, _mk = client_app
        uid = _mk()
        with db.get_db() as c:
            c.execute(
                """INSERT INTO videos (video_url, status, parsed_by, created_at, updated_at)
                   VALUES (?, 'ready', NULL, '2026-01-01T00:00:00+00:00',
                           '2026-01-01T00:00:00+00:00')""",
                (URL,),
            )

        r = probe(app, uid)

        assert r.json() == {"exists": True, "can_regenerate": False}, (
            "无主的内容被判定成有主"
        )


class TestResponseShapeStaysMinimal:
    def test_it_never_returns_the_content_itself(self, client_app, db):
        """这个端点只答「有没有 / 能不能改」。

        顺手把总结、字幕、思维导图也塞回来的话，它就成了第二条
        内容读出口——而那条路不扣额度、不限流、鉴权口径还得再对一次。
        """
        app, _mk = client_app
        owner = _mk()
        seed_community_video(
            URL, summary="SENTINEL_总结", mindmap="SENTINEL_导图",
            subtitle="SENTINEL_字幕", user_id=owner,
        )

        r = probe(app, owner)
        body = r.text

        assert r.json().keys() == {"exists", "can_regenerate"}, r.json()
        for sentinel in ("SENTINEL_总结", "SENTINEL_导图", "SENTINEL_字幕"):
            assert sentinel not in body, f"内容从这条路漏了出来：{sentinel}"
