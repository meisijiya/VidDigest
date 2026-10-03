"""社区列表（公开）+ 详情（需登录）+ 搜索（工单 #7）。

本文件逐条覆盖票面的验收标准。测法有两条硬规矩：

1. **走真实 HTTP 层**（seams.make_client）。直接调路由函数时 user 参数拿到的是
   依赖对象本身，「传个空 user 测 401」只是手写了依赖的返回值，鉴权根本没被执行。
   make_client 不覆写鉴权依赖：带不带 Authorization 头决定身份，两种结果必须不同。

2. **未登录的可见性用键集合断言，不��「某个键不在」**。本文件里
   test_anonymous_card_key_set_is_exactly_the_whitelist 断言的是
   set(item.keys()) == {...}，并且另有一条把哨兵字符串在**整个响应体原文**里
   扫一遍。后者能抓住「键名对但值里带了内容」这种逐个断言抓不到的泄漏。

断言一律读外部可观察的结果（HTTP 状态码、响应体），不测私有函数、
不断言内部调用顺序。
"""
import json

from pathlib import Path

import pytest

import auth
import database
from seams import auth_headers, make_client

#: 哨兵内容：出现在总结 / 思维导图 / 字幕里，且极不可能与其它文本撞上。
#: 未登录访客的响应体里**不允许出现它们中的任何一个**——含子串都不行。
SECRET_SUMMARY = "SECRET_SUMMARY_MARKER_甲"
SECRET_MINDMAP = "SECRET_MINDMAP_MARKER_乙"
SECRET_SUBTITLE = "SECRET_SUBTITLE_MARKER_丙"

#: 未登录列表项的**全部**键。多一个键就是多泄漏一列，少一个键则是功能坏了。
#: id / video_url 是标识不是内容：卡片要能点开、详情要能被寻址。
EXPECTED_CARD_KEYS = {"id", "video_url", "cover_url", "video_title", "tags"}

#: 本文件要断言 database.py 里的某个函数**已经不存在**，需要读源码。
ROOT_BACKEND = Path(__file__).resolve().parent.parent

#: 绝不允许出现在未登录响应里的键名。
FORBIDDEN_KEYS = {"summary_md", "mindmap_md", "subtitle_text", "subtitle_data",
                  "summary_preview", "video_data", "chat_history", "status"}


@pytest.fixture()
def client_app(db, make_user):
    """真实 app + 真实鉴权依赖，只把数据库指向临时库。"""
    import main as main_module
    return main_module.app, make_user


def seed_community_video(url, title="", cover="", tags=None, summary=SECRET_SUMMARY,
                        mindmap=SECRET_MINDMAP, subtitle=SECRET_SUBTITLE, user_id=None):
    """按工单 #6 的真实协议造一条**已就绪**的社区视频。

    刻意走 reserve → complete → publish 三步，而不是直接 INSERT videos：
    直接插入的行绕过了状态机与触发器，测的就不是真实路径了
    （尤其是 publish 写标题时依赖 videos_fts 的 UPDATE 触发器）。
    """
    outcome, _ = database.reserve_video(url, user_id)
    assert outcome == "reserved", f"前提不成立：{url} 被 reserve 判成 {outcome}"
    assert database.complete_video(
        url, summary_md=summary, mindmap_md=mindmap,
        tags=tags or [], subtitle_text=subtitle,
    ) == 1
    if title or cover:
        assert database.publish_video_card(url, title, cover) == 1
    return database.get_video_by_url(url)["id"]


@pytest.fixture()
def seeded(db):
    """三条已就绪的社区视频，外加一条 pending 占位。

    占位必须存在：它是「不要把占位当内容展示」那条要求的唯一考验对象。
    """
    seed_community_video(
        "https://www.bilibili.com/video/BV1aa411c7mD",
        title="深入理解 Python 异步编程",
        cover="https://img.example/cover-1.jpg",
        tags=["编程", "人工智能"],
    )
    seed_community_video(
        "https://youtu.be/dQw4w9WgXcQ",
        title="机器学习入门从零到一",
        cover="https://img.example/cover-2.jpg",
        tags=["人工智能", "其他"],
    )
    seed_community_video(
        "https://example.com/v/cooking",
        title="十分钟学会做饭",
        cover="",
        tags=["生活"],
    )
    database.reserve_video("https://example.com/v/pending", 1)
    return True


def anon(app):
    return make_client(app)


def as_user(app, uid, email="u@example.com"):
    return make_client(app), auth_headers(auth.create_token(uid, email))


# ── AC 1：未登录访问社区列表返回 200 ──────────────────────────

class TestAnonymousList:
    def test_list_returns_200(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/videos")
        assert r.status_code == 200, f"未登录访客看公开列表应得 200，实得 {r.status_code}"

    def test_list_card_key_set_is_exactly_the_whitelist(self, client_app, seeded):
        """键集合**恰好**等于白名单——不是「该不在的不在」。

        逐个断言「summary_md 不在」的话，将来有人加一个 summary_preview
        照样能过；而加一列正是这张列表最可能出的事。
        """
        app, _mk = client_app
        with anon(app) as c:
            body = c.get("/api/community/videos").json()
        assert body["items"], "前提不成立：列表为空，键集合断言无意义"
        for item in body["items"]:
            assert set(item.keys()) == EXPECTED_CARD_KEYS, (
                f"列表项键集合与白名单不符：多出 "
                f"{set(item.keys()) - EXPECTED_CARD_KEYS}，"
                f"缺少 {EXPECTED_CARD_KEYS - set(item.keys())}"
            )
            assert not (set(item.keys()) & FORBIDDEN_KEYS)

    def test_list_carries_cover_title_and_tags(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            items = c.get("/api/community/videos").json()["items"]
        by_title = {i["video_title"]: i for i in items}
        assert "深入理解 Python 异步编程" in by_title
        assert by_title["深入理解 Python 异步编程"]["cover_url"] == \
            "https://img.example/cover-1.jpg"
        assert by_title["深入理解 Python 异步编程"]["tags"] == ["编程", "人工智能"]

    def test_list_omits_pending_placeholder(self, client_app, seeded):
        """pending 是占位：里面没有内容，不能当社区内容列出来。"""
        app, _mk = client_app
        with anon(app) as c:
            body = c.get("/api/community/videos").json()
        assert body["total"] == 3, "三条已就绪的视频应全部可见"
        assert all("pending" not in json.dumps(i) for i in body["items"])


# ── AC 9：列表不泄漏字幕 / 总结 / 思维导图 ──────────────────

class TestAnonymousListLeaksNothing:
    def test_no_content_substring_anywhere_in_raw_body(self, client_app, seeded):
        """在整个响应体**原文**里扫哨兵。

        比逐个键断言更狠：键名对但值里夹带内容（标题字段里拼进总结、
        标签里塞进字幕）这种泄漏，只有原文扫描抓得住。
        """
        app, _mk = client_app
        with anon(app) as c:
            raw = c.get("/api/community/videos").text
        for marker, field in (
            (SECRET_SUMMARY, "总结"),
            (SECRET_MINDMAP, "思维导图"),
            (SECRET_SUBTITLE, "字幕"),
        ):
            assert marker not in raw, f"未登录访客的列表响应体里出现了{field}内容"

    def test_content_absent_for_every_page_and_tag_filter(self, client_app, seeded):
        """翻页与筛选后的每一种响应都不许带内容——泄漏不该只在第一页。"""
        app, _mk = client_app
        with anon(app) as c:
            for params in ({}, {"page": 2}, {"page_size": 1}, {"tag": "人工智能"},
                           {"tag": "编程", "page": 1, "page_size": 1}):
                r = c.get("/api/community/videos", params=params)
                assert r.status_code == 200, f"未登录访客筛选/翻页 {params} 应得 200"
                body = r.json()
                for item in body["items"]:
                    assert set(item.keys()) == EXPECTED_CARD_KEYS, f"参数 {params} 下泄漏"
                for marker in (SECRET_SUMMARY, SECRET_MINDMAP, SECRET_SUBTITLE):
                    assert marker not in r.text, f"参数 {params} 下泄漏"

    def test_logged_in_list_has_the_same_shape(self, client_app, seeded, make_user):
        """列表对登录与否返回**同一种形状**。

        票面写的是「未登录可见封面标题标签」，没说已登录能多看。
        若哪天给已登录列表多塞了内容，这条会红——那时要重新讨论可见性表，
        而不是顺手把断言放宽。
        """
        app, mk = client_app
        uid = mk()
        with anon(app) as c:
            anon_items = c.get("/api/community/videos").json()["items"]
        _c, headers = as_user(app, uid)
        with _c:
            authed_items = make_client(app).get(
                "/api/community/videos", headers=headers
            ).json()["items"]
        assert len(anon_items) == len(authed_items)
        for a, b in zip(anon_items, authed_items):
            assert set(a.keys()) == set(b.keys()) == EXPECTED_CARD_KEYS


# ── AC 2：未登录访问详情返回 401 ──────────────────────────────

class TestAnonymousDetailIs401:
    def test_detail_is_401_not_empty(self, client_app, seeded):
        """「需登录」= 401，不是返回空、也不是返回部分数据。"""
        app, _mk = client_app
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        with anon(app) as c:
            r = c.get(f"/api/community/videos/{vid}")
        assert r.status_code == 401, f"未登录访问详情应得 401，实得 {r.status_code}"

    def test_401_body_carries_no_content(self, client_app, seeded):
        app, _mk = client_app
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        with anon(app) as c:
            r = c.get(f"/api/community/videos/{vid}")
        for marker in (SECRET_SUMMARY, SECRET_MINDMAP, SECRET_SUBTITLE):
            assert marker not in r.text, "401 响应体里出现了内容"

    def test_invalid_token_is_not_logged_in(self, client_app, seeded):
        app, _mk = client_app
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        with anon(app) as c:
            r = c.get(f"/api/community/videos/{vid}",
                      headers=auth_headers("not-a-real-token"))
        assert r.status_code == 401, "无效 token 不该被当成已登录"

    def test_token_of_deleted_user_is_401(self, client_app, seeded, make_user):
        """token 有效但用户已不存在：仍须 401，不能因为解码成功就放行。"""
        app, mk = client_app
        uid = mk("ghost@example.com")
        headers = auth_headers(auth.create_token(uid, "ghost@example.com"))
        with database.get_db() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        with anon(app) as c:
            r = c.get(f"/api/community/videos/{vid}", headers=headers)
        assert r.status_code == 401


# ── AC 3：翻页与标签筛选，未登录也可用 ────────────────────────

class TestAnonymousPaginationAndTagFilter:
    def test_tag_filter_works_for_anonymous(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            hit = c.get("/api/community/videos", params={"tag": "人工智能"}).json()
            miss = c.get("/api/community/videos", params={"tag": "不存在的标签"}).json()
        assert hit["total"] == 2
        assert all("人工智能" in i["tags"] for i in hit["items"])
        assert miss["total"] == 0

    def test_tag_filter_finds_two_character_tags(self, client_app, seeded):
        """「编程」「生活」都是 2 字标签。

        这条同时钉住一件事：标签筛选走的是精确匹配而不是全文检索——
        trigram 匹配不到 2 字词（实测召回 0），若标签筛选也走 FTS，
        词表里大部分 2 字标签就永远筛不出来。
        """
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/videos", params={"tag": "编程"}).json()
        assert r["total"] == 1
        assert r["items"][0]["video_title"] == "深入理解 Python 异步编程"

    def test_pagination_walks_every_item_exactly_once(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            p1 = c.get("/api/community/videos", params={"page": 1, "page_size": 2}).json()
            p2 = c.get("/api/community/videos", params={"page": 2, "page_size": 2}).json()
            p3 = c.get("/api/community/videos", params={"page": 2, "page_size": 2}).json()
        assert p1["total"] == p2["total"] == 3
        assert p1["total_pages"] == 2
        assert len(p1["items"]) == 2 and len(p2["items"]) == 1
        ids = [i["id"] for i in p1["items"] + p2["items"]]
        assert len(set(ids)) == 3, f"翻页漏了或重了：{ids}"
        assert p2["items"] == p3["items"], "同一页两次请求结果不同：排序不确定"

    def test_page_size_is_capped(self, client_app, seeded):
        """页长必须封顶：它直接进 LIMIT，超大值会让数据库白扫整表。

        归一在数据层（_clamp_page）而不是 Query(le=...)：规则只有一份，
        接口与直接调用数据层的行为因此一致。
        """
        app, _mk = client_app
        with anon(app) as c:
            huge = c.get("/api/community/videos", params={"page_size": 100000}).json()
            zero = c.get("/api/community/videos", params={"page_size": 0}).json()
            neg = c.get("/api/community/videos", params={"page": -5}).json()
        assert huge["page_size"] == database.COMMUNITY_PAGE_SIZE_MAX
        assert zero["page_size"] >= 1
        assert neg["page"] == 1

    def test_out_of_range_page_is_empty_not_500(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/videos", params={"page": 9999, "page_size": 10})
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_page_beyond_int64_is_empty_not_500(self, client_app, seeded):
        """页码溢出到 SQLite int64 之外必须是 200 + 空，不是 500。

        `?page=9223372036854775807` 曾经是一个**匿名可触发**的 500：
        Python 整数任意精度，页码没有上界时 (page-1)*page_size 绑不进
        SQLite 的 64 位 INTEGER，sqlite3 抛 OverflowError，路由层没捕获。
        社区列表是公开入口，所以打它不需要登录。

        刻意与上面那条 page=9999 分开写：9999 落在正常 int 范围内，
        压根走不到绑定失败那一步——把两者并进一条，改回「无上界」实现时
        这条仍然是绿的，看起来像已经覆盖了。
        """
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/videos",
                      params={"page": 2 ** 63 - 1, "page_size": 10})
        assert r.status_code == 200, f"页码溢出被打成了 {r.status_code}"
        assert r.json()["items"] == []

    def test_search_page_beyond_int64_is_not_500(self, client_app, seeded, make_user):
        """搜索端点的分页参数走同一条 _paginate，同样不能溢出。

        列表那条绿了不代表搜索也绿：两个路由各自把 page 传进 _paginate，
        少一个上界就只漏一个端点。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search",
                           params={"q": "Python", "page": 10 ** 20}, headers=headers)
        assert r.status_code == 200, f"搜索的页码溢出被打成了 {r.status_code}"
        assert r.json()["items"] == []


# ── AC 4：已登录访问详情返回完整内容 ──────────────────────────

class TestLoggedInDetail:
    def test_detail_returns_summary_mindmap_tags(self, client_app, seeded, make_user):
        app, mk = client_app
        uid = mk()
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        client, headers = as_user(app, uid)
        with client:
            r = client.get(f"/api/community/videos/{vid}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["summary_md"] == SECRET_SUMMARY
        assert body["mindmap_md"] == SECRET_MINDMAP
        assert body["subtitle_text"] == SECRET_SUBTITLE
        assert body["tags"] == ["编程", "人工智能"]
        assert body["video_title"] == "深入理解 Python 异步编程"
        assert body["cover_url"] == "https://img.example/cover-1.jpg"

    def test_any_logged_in_user_can_read(self, client_app, seeded, make_user):
        """谁都能读社区那一份——不是只有首次解析者能看。"""
        app, mk = client_app
        vid = database.get_video_by_url(
            "https://www.bilibili.com/video/BV1aa411c7mD")["id"]
        for email in ("a@example.com", "b@example.com"):
            uid = mk(email)
            client, headers = as_user(app, uid, email)
            with client:
                r = client.get(f"/api/community/videos/{vid}", headers=headers)
            assert r.status_code == 200, f"{email} 应能读社区内容"
            assert r.json()["summary_md"] == SECRET_SUMMARY

    def test_pending_placeholder_detail_is_404(self, client_app, seeded, make_user):
        """已登录去看别人的占位：404，不是把空壳当内容返回。"""
        app, mk = client_app
        uid = mk()
        pending_id = database.get_video_by_url("https://example.com/v/pending")["id"]
        client, headers = as_user(app, uid)
        with client:
            r = client.get(f"/api/community/videos/{pending_id}", headers=headers)
        assert r.status_code == 404

    def test_missing_video_detail_is_404(self, client_app, seeded, make_user):
        app, mk = client_app
        uid = mk()
        client, headers = as_user(app, uid)
        with client:
            r = client.get("/api/community/videos/999999", headers=headers)
        assert r.status_code == 404


# ── AC 5 / 6 / 7：搜索（关键词 / 链接 / 标签）──────────────────

class TestSearchRequiresLogin:
    def test_anonymous_search_is_401(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/search", params={"q": "异步"})
        assert r.status_code == 401, f"未登录搜索应得 401，实得 {r.status_code}"

    def test_401_body_carries_no_content(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/search", params={"q": "异步"})
        for marker in (SECRET_SUMMARY, SECRET_MINDMAP, SECRET_SUBTITLE):
            assert marker not in r.text

    def test_invalid_token_cannot_search(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get("/api/community/search", params={"q": "异步"},
                      headers=auth_headers("not-a-real-token"))
        assert r.status_code == 401


class TestSearchByTitleKeyword:
    def test_chinese_keyword_finds_video(self, client_app, seeded, make_user):
        """中文关键词必须真的能召回（这正是要用 trigram 的原因）。"""
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "机器学习"}, headers=headers)
        assert r.status_code == 200
        items = r.json()["items"]
        assert [i["video_title"] for i in items] == ["机器学习入门从零到一"]

    def test_partial_chinese_substring_finds_video(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "学会做饭"},
                           headers=headers)
        assert [i["video_title"] for i in r.json()["items"]] == ["十分钟学会做饭"]

    def test_latin_keyword_finds_video(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "Python"}, headers=headers)
        assert [i["video_title"] for i in r.json()["items"]] == ["深入理解 Python 异步编程"]

    def test_keyword_without_hit_returns_empty(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "量子计算喷气推进"},
                           headers=headers)
        assert r.status_code == 200
        assert r.json()["items"] == [] and r.json()["total"] == 0

    def test_search_results_are_cards_not_details(self, client_app, seeded, make_user):
        """搜索结果也是卡片，不因为已登录就把总结塞进列表响应。"""
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "机器学习"}, headers=headers)
        for item in r.json()["items"]:
            assert set(item.keys()) == EXPECTED_CARD_KEYS
        assert SECRET_SUMMARY not in r.text


class TestSearchByExactUrl:
    def test_exact_url_locates_that_video(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get(
                "/api/community/search",
                params={"q": "https://youtu.be/dQw4w9WgXcQ"}, headers=headers)
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["video_title"] == "机器学习入门从零到一"
        assert body["mode"] == "url"

    def test_url_lookup_of_another_video_returns_nothing(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get(
                "/api/community/search",
                params={"q": "https://example.com/v/nope"}, headers=headers)
        assert r.json()["total"] == 0

    def test_url_lookup_does_not_return_pending_placeholder(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get(
                "/api/community/search",
                params={"q": "https://example.com/v/pending"}, headers=headers)
        assert r.json()["total"] == 0, "占位不是社区内容"

    def test_search_matches_url_as_text_when_not_a_full_url(self, client_app, seeded, make_user):
        """不带 scheme 的片段走文本检索：用户在地址栏里常只粘一部分。"""
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "dQw4w9WgXcQ"},
                           headers=headers)
        assert r.json()["total"] == 1


class TestSearchByTag:
    def test_tag_search_finds_all_tagged(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"tag": "人工智能"}, headers=headers)
        items = r.json()["items"]
        assert len(items) == 2
        assert all("人工智能" in i["tags"] for i in items)

    def test_tag_search_finds_two_character_tag(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"tag": "生活"}, headers=headers)
        assert [i["video_title"] for i in r.json()["items"]] == ["十分钟学会做饭"]

    def test_keyword_and_tag_intersect(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            hit = client.get("/api/community/search",
                             params={"q": "机器学习", "tag": "人工智能"}, headers=headers)
            miss = client.get("/api/community/search",
                              params={"q": "机器学习", "tag": "编程"}, headers=headers)
        assert hit.json()["total"] == 1
        assert miss.json()["total"] == 0, "两个条件是取交集，不是并集"

    def test_tag_only_search_paginates(self, client_app, seeded, make_user):
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search",
                           params={"tag": "人工智能", "page": 1, "page_size": 1},
                           headers=headers)
        body = r.json()
        assert body["total"] == 2 and len(body["items"]) == 1


# ── AC 8：搜索基于 FTS5 + trigram，不是朴素模糊匹配 ──────────

class TestSearchIsFullTextNotLike:
    def test_latin_match_is_case_insensitive(self, client_app, seeded, make_user):
        """小写 python 能命中标题里的 Python。

        ⚠️ 这条**不能**用来区分 FTS 与 LIKE：SQLite 的 LIKE 对 ASCII 本来就是
        大小写不敏感的，两种实现都会通过。真正的区分证据是下面那条
        「2 字词召回 0」——已用变异验证过：把 FTS 换成 LIKE，它会立刻变红。
        这里只守住「拉丁词条能正常召回」这条基本能力。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "python"}, headers=headers)
        assert r.json()["total"] == 1, "小写关键词搜不到标题里的 Python"

    def test_two_character_term_is_below_the_trigram_floor(self, client_app, seeded, make_user):
        """2 字中文召回 0——trigram 的下界，不是实现漏了。

        这条是「FTS5 + trigram」与「朴素 LIKE」之间**真正**的区分证据
        （已变异验证：把 MATCH 换成 LIKE，本条立刻变红）。LIKE 能匹配 2 字词，
        trigram 不能——它在索引里根本没有 2 字词对应的三元组。
        它钉住的是 trigram 存在的签名，不是缺陷：2 字词的入口是按标签精确筛选。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "编程"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["total"] == 0, (
            "2 字中文命中了：全文检索被换成了 LIKE 之类的朴素匹配"
        )

    def test_short_query_says_why_it_found_nothing(
        self, client_app, seeded, make_user
    ):
        """短查询查不到是 trigram 的地板，但界面必须把这件事说出来。

        上一条钉住的是「不该发生什么」（不会静默退化成 LIKE 匹配）。
        这条钉住该发生什么：接口照常 200 + 0 命中，同时把边界一起返回
        （q_too_short / min_chars）。不说的话，用户看到的就是「搜了没反应」，
        与「社区里真没有」在界面上完全一样。

        三个模式分开验：URL 精确定位走等值、浏览模式不过滤，
        长度都不构成限制，只有走 FTS 的关键词模式受这个地板约束。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            short = client.get("/api/community/search",
                               params={"q": "编程"}, headers=headers).json()
            long_enough = client.get("/api/community/search",
                                     params={"q": "机器学习"}, headers=headers).json()
            by_url = client.get("/api/community/search",
                                params={"q": "https://youtu.be/dQw4w9WgXcQ"},
                                headers=headers).json()
        assert short["q_too_short"] is True, "2 字查询没有告诉前端它必然召不回"
        assert short["min_chars"] == database.MIN_FTS_TERM_CHARS
        assert long_enough["q_too_short"] is False
        assert by_url["q_too_short"] is False, "URL 精确定位走等值匹配，不该被判成短查询"

    def test_fts_syntax_in_user_input_does_not_break_search(self, client_app, seeded, make_user):
        """用户输入里的 FTS 语法符与控制字符不能把搜索打成 500。

        实测裸传这些串会让 FTS5 抛 OperationalError（unterminated string /
        syntax error）。端点必须是 200 + 0 命中，而不是 500。

        NUL 单列一组：它不是「语法错」而是「字符串未闭合」，机制与引号、
        运算符都不同，混在一张列表里日后很容易被当成同一种东西删掉。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        hostile = ['"unterminated', 'a OR', "foo'bar", 'x AND y', 'NEAR(a b)',
                   'a*b', '*', '^', '{', '编程 OR', 'NOT x', '\\',
                   '\x00', 'ab\x00cd', '\x00abc']
        with client:
            for q in hostile:
                r = client.get("/api/community/search", params={"q": q}, headers=headers)
                assert r.status_code == 200, f"q={q!r} 把搜索打成了 {r.status_code}"
                assert r.json()["total"] == 0, f"q={q!r} 不该有任何命中"

    def test_blank_query_degrades_to_browse_not_an_error(self, client_app, seeded, make_user):
        """纯空白查询退化成浏览——这是对的，但它与 NUL 不是一回事。

        `\\n` / `\\t` / `\\x1f` 在 Python 的 str.strip() 里都算空白，
        查询词归一后为空，于是走浏览分支返回全部。它们不需要 _fts_phrase
        剥（根本没进 FTS），NUL 则不是空白，必须剥掉否则 500。

        刻意不把空白类塞进上面那张敌意表：那张表断言 total == 0，
        套到空白上就等于要求「搜了个空的」返回空列表——那比返回全部更糟，
        用户会以为整个社区空了。
        """
        app, mk = client_app
        client, headers = as_user(app, mk())
        with client:
            for q in ("\n", "\t", "\x1f", "   "):
                r = client.get("/api/community/search", params={"q": q}, headers=headers)
                assert r.status_code == 200, f"q={q!r} 把搜索打成了 {r.status_code}"
                assert r.json()["mode"] == "browse", (
                    f"q={q!r} 归一后为空，应当退化成浏览而不是当成关键词"
                )

    def test_index_is_fts5_with_trigram_tokenizer(self, client_app, seeded):
        """AC 原文点名了机制，这里就查机制本身。

        这是本文件里唯一一条结构断言：AC 明确要求「SQLite 的 FTS5 与
        trigram 分词器」，那么索引是不是一张 trigram 分词的 FTS5 表就是
        契约的一部分，不是实现细节。上面几条行为断言负责证明它**能用**，
        这条负责证明它**是那一个**。
        """
        with database.get_db() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = ?",
                (database._VIDEO_SEARCH_INDEX,),
            ).fetchone()["sql"]
        lowered = sql.lower()
        assert "using fts5" in lowered, f"搜索索引不是 FTS5 表：{sql}"
        assert "trigram" in lowered, f"搜索索引没用 trigram 分词器：{sql}"

    def test_all_three_fts_triggers_exist(self, client_app, seeded):
        """三个同步触发器必须都在——这条直接查 sqlite_master。

        它补的是行为断言的盲区：只要测试期间有任何东西重建过索引
        （最典型的是 TestClient 进 `with` 上下文后跑的 lifespan →
        init_db()，它会重建缺失的触发器并全量 rebuild），
        「删掉全部三个触发器」这个变异依然是绿的。

        这条不依赖任何一次请求，因此不会跟着 lifespan 的行为漂移。
        触发器少任何一个都会红：INSERT / UPDATE / DELETE 各管一头，
        少一个就是某条写路径上的索引静默失效。
        """
        expected = {"videos_fts_ai", "videos_fts_au", "videos_fts_ad"}
        with database.get_db() as c:
            found = {
                r["name"] for r in c.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type = 'trigger' AND name LIKE 'videos_fts_%'"
                )
            }
        assert found == expected, (
            f"FTS 同步触发器不齐：缺 {expected - found}，多 {found - expected}。"
            "缺任何一个，索引都会在对应的写路径上静默失效"
        )

    def test_search_survives_a_fresh_init_db(self, client_app, seeded, make_user):
        """rebuild 与触发器是幂等的：重新建库后索引仍可用。"""
        app, mk = client_app
        database.init_db()
        client, headers = as_user(app, mk())
        with client:
            r = client.get("/api/community/search", params={"q": "机器学习"}, headers=headers)
        assert r.json()["total"] == 1

    def test_retitled_video_is_found_under_the_new_title_only(
        self, client_app, seeded, make_user
    ):
        """标题一变，旧标题立刻搜不到——索引真的跟着数据走了。

        刻意用**直连 SQL** 改标题，而不是走回填接口：回填只填空不覆盖
        （见 TestBackfillFillsGapsButNeverOverwrites），而索引触发器必须对
        任何 UPDATE 都生效，不只是对某条 API 路径生效。

        **这里不能写 `with client:`。** TestClient 进上下文会跑 lifespan，
        lifespan 调 init_db()，而 init_db() 会重建缺失的触发器并全量
        rebuild——于是「删掉全部三个触发器」这个变异在本测试里仍然是绿的：
        它测到的不是触发器在工作，而是「有东西会重建索引」。
        实测确认：删光三个触发器后本测试照过，去掉上下文管理器后才会红。
        """
        app, mk = client_app
        with database.get_db() as c:
            c.execute(
                "UPDATE videos SET video_title = ? WHERE video_url = ?",
                ("换过的全新标题", "https://www.bilibili.com/video/BV1aa411c7mD"),
            )
        client, headers = as_user(app, mk())
        old = client.get("/api/community/search", params={"q": "Python"}, headers=headers)
        new = client.get("/api/community/search", params={"q": "全新标题"}, headers=headers)
        assert old.json()["total"] == 0, "旧标题仍能搜到：索引没跟着更新"
        assert new.json()["total"] == 1


# ── by-url 只有一条读出口（ADR 0007 修订后的收敛）────────────────

BY_URL = "/api/community/videos/by-url"


class TestSingleByUrlTruthSource:
    """「社区里有没有这一条」在全仓只有一个答案。"""

    def test_hits_community_without_any_personal_record(
        self, client_app, seeded, make_user
    ):
        """社区里有、但这个人从没解析过 → 仍应命中。

        这正是「查个人历史表」会漏掉的那类人：他们会以为社区里没有，
        于是去重新解析，白花一次额度。
        """
        app, mk = client_app
        uid = mk("fresh@example.com")
        client, headers = as_user(app, uid, "fresh@example.com")
        with client:
            r = client.get(BY_URL,
                           params={"url": "https://youtu.be/dQw4w9WgXcQ"},
                           headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["exists"] is True

    def test_ignores_personal_history_alone(self, client_app, make_user):
        """个人历史里有、社区里没有 → 不算命中，且不得回显个人那份内容。

        这是要消灭的那个洞：判据若落在个人表上，前端会直接渲染他那份
        summary_md，社区里别人解析出的同一份内容就再也到不了他眼前。
        """
        app, mk = client_app
        uid = mk("solo@example.com")
        database.upsert_parse_history(
            user_id=uid,
            video_url="https://example.com/v/only-mine",
            video_title="只有我解析过",
            summary_md=SECRET_SUMMARY,
            mindmap_md=SECRET_MINDMAP,
        )
        client, headers = as_user(app, uid, "solo@example.com")
        with client:
            r = client.get(BY_URL,
                           params={"url": "https://example.com/v/only-mine"},
                           headers=headers)
        assert r.json()["exists"] is False, "个人历史被当成了社区内容"
        assert SECRET_SUMMARY not in r.text
        assert SECRET_MINDMAP not in r.text

    def test_returns_existence_and_permission_only(self, client_app, seeded, make_user):
        """by-url 只回答「在不在」与「能不能改」，不返回任何内容。

        多带一列内容出来，就多一条内容读出口——而那条路不扣额度、
        鉴权口径还要再对一次。
        """
        app, mk = client_app
        uid = mk()
        client, headers = as_user(app, uid)
        with client:
            r = client.get(BY_URL,
                           params={"url": "https://youtu.be/dQw4w9WgXcQ"},
                           headers=headers)
        assert set(r.json().keys()) == {"exists", "can_regenerate"}, r.json()
        for marker in (SECRET_SUMMARY, SECRET_MINDMAP, SECRET_SUBTITLE):
            assert marker not in r.text

    def test_misses_while_placeholder_pending(self, client_app, seeded, make_user):
        """占位不算命中：社区里还没有结果，前端应当去解析。"""
        app, mk = client_app
        uid = mk()
        client, headers = as_user(app, uid)
        with client:
            r = client.get(BY_URL,
                           params={"url": "https://example.com/v/pending"},
                           headers=headers)
        assert r.json()["exists"] is False

    def test_still_requires_login(self, client_app, seeded):
        app, _mk = client_app
        with anon(app) as c:
            r = c.get(BY_URL, params={"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r.status_code == 401

    def test_there_is_exactly_one_by_url_read_outlet(self):
        """守卫本身：别让第二条 by-url 读出口再长出来。

        这条是本文件存在的理由。上一次分叉（同一问题两条端点、形状不同）
        直接让我把根因判反了——而当时**所有测试都是绿的**。
        """
        import api_community
        import api_history

        routes = []
        for mod in (api_community, api_history):
            for r in mod.router.routes:
                path = getattr(r, "path", "")
                if path.endswith("by-url"):
                    routes.append(f"{mod.__name__}{path}")
        assert routes == ["api_community" + BY_URL], (
            f"by-url 读出口不止一条：{routes}。它们答同一个问题，"
            "形状还不一样，下一个人只改一条就会分叉。"
        )

    def test_the_dead_helper_is_gone_from_the_data_layer(self):
        """get_community_video_by_url 已删：它只被那条死路由用。"""
        src = (ROOT_BACKEND / "database.py").read_text(encoding="utf-8")
        assert "def get_community_video_by_url" not in src, (
            "get_community_video_by_url 还在。它现在没有任何调用方，"
            "留着只会让人以为它是另一条读出口。"
        )


# ── 卡片回填 ─────────────────────────────────────────────────

class TestPublishCard:
    def test_publish_fills_title_and_cover(self, client_app, make_user):
        app, mk = client_app
        uid = mk()
        database.reserve_video("https://example.com/v/new", uid)
        database.complete_video("https://example.com/v/new", summary_md=SECRET_SUMMARY,
                                tags=["编程"], subtitle_text=SECRET_SUBTITLE)
        client, headers = as_user(app, uid)
        with client:
            r = client.post("/api/community/cards", headers=headers, json={
                "url": "https://example.com/v/new",
                "video_title": "回填的标题",
                "cover_url": "https://img.example/new.jpg",
            })
            listed = client.get("/api/community/videos", headers=headers).json()
        assert r.status_code == 200
        item = next(i for i in listed["items"] if i["video_url"] == "https://example.com/v/new")
        assert item["video_title"] == "回填的标题"
        assert item["cover_url"] == "https://img.example/new.jpg"

    def test_publish_cannot_forge_content(self, client_app, make_user):
        """回填接口**只能填空**，写不进总结、字幕或标签，也改不动已填的标题。

        少一个字段它就写不进去——这是「ready 行谁都不能改写」不被从
        后门捅穿的原因。标题同样不可改：它是用户判断内容与搜索的入口，
        能被任意登录用户重写就等于开了一条改社区的路子。
        """
        app, mk = client_app
        uid = mk()
        vid = seed_community_video("https://example.com/v/guarded",
                                   title="原标题", tags=["编程"],
                                   user_id=uid)
        client, headers = as_user(app, uid)
        with client:
            r = client.post("/api/community/cards", headers=headers, json={
                "url": "https://example.com/v/guarded",
                "video_title": "改了标题",
                "summary_md": "伪造的总结",
            })
            detail = client.get(f"/api/community/videos/{vid}", headers=headers).json()
        assert r.status_code == 200
        assert detail["summary_md"] == SECRET_SUMMARY, "回填接口能改写总结内容"
        assert detail["tags"] == ["编程"], "回填接口能改写标签"
        assert detail["video_title"] == "原标题", "回填接口能改写已填的标题"

    def test_publish_requires_login(self, client_app, make_user):
        app, mk = client_app
        uid = mk()
        seed_community_video("https://example.com/v/anon", user_id=uid)
        with anon(app) as c:
            r = c.post("/api/community/cards", json={
                "url": "https://example.com/v/anon", "video_title": "x"})
        assert r.status_code == 401


# ── 卡片回填：只能填空，不能改（工单 #7 收口）────────────────

class TestBackfillFillsGapsButNeverOverwrites:
    """回填是「补」不是「改」。

    标题与封面是用户判断内容和搜索的入口。任何登录用户都能改写它，
    等于给了一条「把别人的视频改成别的样子」的路子，还会静默改动 FTS
    索引——与父工单 #1 的「社区内容不会被别人改写」直接冲突。
    """

    @staticmethod
    def _ready(url: str = "https://example.com/known") -> None:
        database.reserve_video(url, 1)
        database.complete_video(url, summary_md="社区里那一份")

    def test_first_backfill_wins(self, db):
        self._ready()
        assert database.publish_video_card(
            "https://example.com/known", "原始标题", "https://img/a.jpg"
        ) == 1
        row = database.get_video_by_url("https://example.com/known")
        assert row["video_title"] == "原始标题"
        assert row["cover_url"] == "https://img/a.jpg"

    def test_updated_is_false_when_nothing_actually_changed(self, db):
        """返回值必须诚实：一个字段都没写进去就不能说写了。

        SQLite 的 changes() 统计的是「被 UPDATE 语句触及的行」，
        `SET col='same'` 也算 1。所以 WHERE 里只写「至少一列为空」是不够的：
        一个只想补封面、而封面早就填好的调用者会拿到 updated=1，
        而实际上什么字段都没动。

        当前前端不读这个字段（它 `.catch(() => {})` 一路吞掉），
        但它是接口承诺的一部分：下一个按它做去重或统计的调用者会被带偏。

        场景：先只填封面、标题留空；第二个调用者同样只传封面。
        它既没填到标题（没传非空标题），也覆盖不了已填的封面。
        """
        self._ready()
        url = "https://example.com/known"
        assert database.publish_video_card(url, "", "https://img/first.jpg") == 1
        assert database.publish_video_card(url, "", "https://img/second.jpg") == 0, (
            "一个字段都没写进去，却报告 updated=1"
        )
        row = database.get_video_by_url(url)
        assert row["cover_url"] == "https://img/first.jpg", "第二个调用者覆盖了先到者的封面"

    def test_second_backfill_cannot_rewrite(self, db):
        """关键：第二个人的回填必须被拒绝，已填的值一字不动。"""
        self._ready()
        database.publish_video_card("https://example.com/known", "原始标题", "https://img/a.jpg")
        assert database.publish_video_card(
            "https://example.com/known", "被改掉的标题", "https://img/evil.jpg"
        ) == 0, "第二次回填不该改写已经填好的字段"
        row = database.get_video_by_url("https://example.com/known")
        assert row["video_title"] == "原始标题", row
        assert row["cover_url"] == "https://img/a.jpg", row

    def test_second_backfill_can_still_fill_the_gap(self, db):
        """只填了标题时，后来者仍能补上封面——这是「填空」不是「禁止」。"""
        self._ready()
        database.publish_video_card("https://example.com/known", "原始标题", "")
        assert database.publish_video_card(
            "https://example.com/known", "", "https://img/b.jpg"
        ) == 1
        row = database.get_video_by_url("https://example.com/known")
        assert row["video_title"] == "原始标题", "补封面不该动到已填的标题"
        assert row["cover_url"] == "https://img/b.jpg"

    def test_pending_row_is_not_backfillable(self, db):
        """占位行还没有社区内容，不能先给它做卡片。"""
        database.reserve_video("https://example.com/pending", 1)
        assert database.publish_video_card(
            "https://example.com/pending", "标题", ""
        ) == 0
        row = database.get_video_by_url("https://example.com/pending")
        assert row["video_title"] == "", row
