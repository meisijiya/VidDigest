"""社区审核：改标签 / 删条目 / 读词表（后台社区页的三个出口）。

沿用 test_admin_user_lifecycle.py 与 test_admin_api.py 的纪律：

1. 走真实 HTTP 层（seams.make_client），**不用 `with client`**——那会跑
   lifespan 里的 init_db()，把迁移类断言遮住（工单 #7 的假护栏）。
2. 安全断言必有正反双向对照：只断「非管理员 403」的话，「全员 403」也能
   让它绿，所以同一个文件里必然还有「管理员 200 且数据非空」。
3. 断言具体值，不是只断言 200。被拒的请求**额外**断言库里的数据没变——
   「400 了却把标签改了」是能返回 200 之外的码的静默损坏。

本文件的主角是删除语义：**只删 videos 一行，parse_history 一行不动**。
视频从社区消失，解析过它的用户在自己的历史里仍看得到自己那条记录。
关键那条断言落在**用户真正会用的读出口**上（GET /api/history），而不是
只查表：只查表的话，哪天这个读出口整个坏掉，断言照样绿——而
「谁的记录都读不到」与「历史跟着社区一起没了」，症状是一样的。
"""
import json
from datetime import datetime, timedelta

import pytest

import auth
import admin_api
import database
import tags
from seams import auth_headers, make_client


@pytest.fixture()
def client_app(db, make_user):
    """真实 app + 真实 client + 一个管理员、一个普通用户。

    刻意不 `with client`（见文件头第 1 条）。
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


def _video_id(url: str) -> int:
    with database.get_db() as conn:
        return conn.execute(
            "SELECT id FROM videos WHERE video_url = ?", (url,)
        ).fetchone()["id"]


def _ready_video(url: str, parsed_by=None, with_tags=None) -> int:
    """走真实写入路径造一条已就绪的社区视频（reserve → complete），返回 id。"""
    database.reserve_video(url, parsed_by)
    database.complete_video(url, summary_md="总结正文", tags=with_tags or ["编程"])
    return _video_id(url)


def _pending_video(url: str, parsed_by=None) -> int:
    """只占位、不写回——就是「有人抢到了但还没解析完」的那种行。"""
    database.reserve_video(url, parsed_by)
    return _video_id(url)


def _stored_row(video_id: int) -> dict:
    """直接读那一行。

    「400 了却把数据改了」这类断言必须读**存储形态**：端点回显的字段是
    投影后的值，拿它去证明「库没被改」是循环论证。
    """
    with database.get_db() as conn:
        return dict(conn.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        ).fetchone())


def _stored_tags(video_id: int) -> list:
    return json.loads(_stored_row(video_id)["tags"])


# ── 改标签 ────────────────────────────────────────────────────

def _frozen_clock(moment):
    """造一个替掉 ``database.datetime`` 的类，把「现在」钉在 moment。

    **继承** ``datetime`` 而不是顶替它：``database`` 别处还要用
    ``datetime.fromisoformat``，一个只实现了 ``now`` 的假类会让那些路径
    **因错误的原因**抛错 -- 那种红不是护栏在响，是测错了东西。
    """

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return moment.replace(tzinfo=None)
            return moment.astimezone(tz)

    return _Clock


class TestTagUpdate:
    def test_tags_land_in_db_and_come_back_in_the_item(self, client_app):
        """改成功后库里是新的 JSON 数组，回读的 item.tags 就是它。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/a", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["人工智能", "前端开发"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert _stored_tags(vid) == ["人工智能", "前端开发"]
        assert r.json()["item"]["tags"] == ["人工智能", "前端开发"]
        # 回读必须与列表项同形状：前端是拿它直接替换列表里那一行的
        assert set(r.json()["item"]) == set(
            client.get("/api/admin/community", headers=_admin_hdr(client_app)
                       ).json()["items"][0]
        ), "PATCH 的 item 与列表项字段集不一致，前端替换后会掉字段"

    def test_out_of_vocabulary_tag_is_400_and_writes_nothing(self, client_app):
        """词表外值 → 400，且**库里的标签没变**。

        模型路径在这里是容错的（回落到「其他」），管理员路径不是：静默把
        「AI编程」变成「其他」会把打错字藏起来。
        """
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/b", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["AI编程"]}, headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert "AI编程" in r.json()["detail"], f"detail 必须点名被拒掉的值：{r.text}"
        assert _stored_tags(vid) == ["编程"], "400 了却把标签改了"

    def test_one_bad_tag_rejects_the_whole_request(self, client_app):
        """混合提交里只要有一个词表外的值，整个请求就拒——不做部分生效。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/c", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["读书", "自创词"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert _stored_tags(vid) == ["编程"], "同一个请求里好标签被部分写进去了"

    def test_empty_array_is_400_and_writes_nothing(self, client_app):
        """每条视频至少 1 个标签是系统不变式，模型路径永不产出空。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/d", plain_id, with_tags=["读书"])

        r = client.patch(f"/api/admin/community/{vid}", json={"tags": []},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert _stored_tags(vid) == ["读书"], "空数组被当成了「清空标签」"

    def test_more_than_max_tags_is_400_and_writes_nothing(self, client_app):
        """超额要报错而不是被截断：validate_tags 只砍到 3 个并返回 200。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/e", plain_id, with_tags=["编程"])
        too_many = ["读书", "健身", "旅行", "摄影"]

        r = client.patch(f"/api/admin/community/{vid}", json={"tags": too_many},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 400, r.text
        assert _stored_tags(vid) == ["编程"], "超额的标签被截断后写进去了"

    def test_order_follows_the_vocabulary_not_the_request(self, client_app):
        """端点必须透传 validate_tags 的词表顺序（展示稳定、不抖）。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/f", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["前端开发", "编程"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        # 词表声明顺序：编程 在 前端开发 之前
        assert r.json()["item"]["tags"] == ["编程", "前端开发"]
        assert _stored_tags(vid) == ["编程", "前端开发"]

    def test_duplicates_collapse(self, client_app):
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/g", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["读书", "读书", "健身"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert r.json()["item"]["tags"] == ["读书", "健身"]

    def test_only_tags_and_updated_at_change(self, client_app, monkeypatch):
        """改标签不许改到内容与状态；updated_at 必须跟着动。

        时间是**钉死**的，不是「等一会儿再看变了没」。那种写法靠的是时钟
        走过了一格，而本机实测：连续两次 ``datetime.now()`` 有 199832/200000
        次返回**完全相同**的值（时钟量化到约 0.3ms）。创建那一行后紧接着
        PATCH，极易落进同一格，于是这条断言成了掷骰子——单跑 5 次全过，
        全量跑偶尔红，而 ``update_video_tags`` 本身并没有错。

        钉死之后断的是「updated_at 被写成了**这次改标签**的时刻」：正向证法
        是值必须**等于**钉死的那一刻，把 UPDATE 里的 updated_at 去掉就会红。
        """
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/h", plain_id, with_tags=["编程"])
        before = _stored_row(vid)

        # 从这一行自己存的值派生，必然不同，且仍在同一天（不触发按日期
        # 重置的那类副作用）。
        moment = datetime.fromisoformat(before["updated_at"]) + timedelta(minutes=7)
        monkeypatch.setattr(database, "datetime", _frozen_clock(moment))

        r = client.patch(f"/api/admin/community/{vid}", json={"tags": ["读书"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        after = _stored_row(vid)

        assert after["tags"] != before["tags"]
        assert after["updated_at"] == moment.isoformat(), (
            f"updated_at 没跟着这次改标签走：期望 {moment.isoformat()!r}，"
            f"实际 {after['updated_at']!r}——后台就分不清这条是刚被维护过的")
        assert after["updated_at"] != before["updated_at"]
        for column in ("status", "summary_md", "mindmap_md", "subtitle_text",
                       "created_at", "parsed_by", "video_url"):
            assert after[column] == before[column], f"改标签竟然动到了 {column}"

    def test_pending_row_tags_are_editable_too(self, client_app):
        """占位行也要能改标签：它的标签要等模型写回，正是人工标注的空档。"""
        client, _, plain_id = client_app
        vid = _pending_video("https://v.example/i", plain_id)

        r = client.patch(f"/api/admin/community/{vid}", json={"tags": ["读书"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert r.json()["item"]["status"] == "pending"
        assert _stored_tags(vid) == ["读书"]

    def test_unknown_video_is_404(self, client_app):
        client, _, _ = client_app
        r = client.patch("/api/admin/community/999999", json={"tags": ["读书"]},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 404, r.text

    def test_unknown_field_is_422_and_writes_nothing(self, client_app):
        """多传一个键（status）→ 422。摘掉 extra="forbid" 它就会返回 200。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/j", plain_id, with_tags=["编程"])

        r = client.patch(f"/api/admin/community/{vid}",
                         json={"tags": ["读书"], "status": "pending"},
                         headers=_admin_hdr(client_app))
        assert r.status_code == 422, r.text
        assert _stored_tags(vid) == ["编程"]

    def test_tags_must_be_a_list(self, client_app):
        """结构错（不是数组）归 pydantic，码是 422 而不是 400。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/k", plain_id, with_tags=["编程"])

        for bad in ("读书", {"a": 1}, 3):
            r = client.patch(f"/api/admin/community/{vid}", json={"tags": bad},
                             headers=_admin_hdr(client_app))
            assert r.status_code == 422, f"tags={bad!r} 应得 422：{r.text}"
        assert _stored_tags(vid) == ["编程"]

    def test_requires_admin(self, client_app):
        """正反对照：只断 403 的话「全员 403」也能让它绿。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/l", plain_id, with_tags=["编程"])

        no = client.patch(f"/api/admin/community/{vid}", json={"tags": ["读书"]},
                          headers=_plain_hdr(client_app))
        anon = client.patch(f"/api/admin/community/{vid}", json={"tags": ["读书"]})
        ok = client.patch(f"/api/admin/community/{vid}", json={"tags": ["读书"]},
                          headers=_admin_hdr(client_app))
        assert (no.status_code, anon.status_code, ok.status_code) == (403, 401, 200)
        assert _stored_tags(vid) == ["读书"], "被拒的请求不该改到数据（200 那次改的）"


# ── 删除 ──────────────────────────────────────────────────────

class TestDeleteCommunityVideo:
    def test_deleted_row_is_gone_from_the_admin_list(self, client_app):
        client, _, plain_id = client_app
        keep = _ready_video("https://v.example/keep", plain_id)
        gone = _ready_video("https://v.example/gone", plain_id)

        r = client.delete(f"/api/admin/community/{gone}",
                          headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": gone}

        ids = [i["id"] for i in client.get(
            "/api/admin/community", headers=_admin_hdr(client_app)).json()["items"]]
        assert gone not in ids, "删完这一行还在列表里"
        assert keep in ids, "删错行了：另一条也被带走"

    def test_it_disappears_from_the_public_community(self, client_app):
        """删除的对外效果：社区里看不见它了。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/pub", plain_id)

        before = client.get("/api/community/videos").json()
        assert [i["id"] for i in before["items"]] == [vid], "前置条件：本来就在社区里"

        assert client.delete(f"/api/admin/community/{vid}",
                             headers=_admin_hdr(client_app)).status_code == 200
        after = client.get("/api/community/videos").json()
        assert [i["id"] for i in after["items"]] == []
        assert after["total"] == 0

    def test_parse_history_survives_the_deletion(self, client_app):
        """**本工单最关键的一条**：用户的解析历史不跟着社区内容一起没。

        断言落在用户自己用的读出口（GET /api/history）上，而不是只查表：
        「历史跟着没了」与「历史这个读出口整个坏了」症状一样，
        只有走这条真实路径才分得开。
        """
        client, _, plain_id = client_app
        url = "https://v.example/history"
        database.upsert_parse_history(plain_id, url, "标题", summary_md="正文")
        vid = _ready_video(url, plain_id)

        before = client.get("/api/history", headers=_plain_hdr(client_app)).json()
        assert [i["video_url"] for i in before["items"]] == [url], "前置条件：历史里本来有"

        assert client.delete(f"/api/admin/community/{vid}",
                             headers=_admin_hdr(client_app)).status_code == 200

        after = client.get("/api/history", headers=_plain_hdr(client_app)).json()
        assert [i["video_url"] for i in after["items"]] == [url], (
            "社区内容一删，用户自己的历史也跟着没了——这是本工单明确不做的级联")
        with database.get_db() as conn:
            assert conn.execute(
                "SELECT count(*) FROM parse_history WHERE user_id = ? AND video_url = ?",
                (plain_id, url)).fetchone()[0] == 1, "parse_history 那一行真的没了"

    def test_chat_messages_are_not_cascaded_either(self, client_app):
        """追问记录也一行不动：本设计只删 videos 一张表。"""
        client, _, plain_id = client_app
        url = "https://v.example/chat"
        vid = _ready_video(url, plain_id)
        with database.get_db() as conn:
            conn.execute(
                "INSERT INTO chat_messages (user_id, video_url, role, content) "
                "VALUES (?, ?, 'user', 'hi')", (plain_id, url))

        assert client.delete(f"/api/admin/community/{vid}",
                             headers=_admin_hdr(client_app)).status_code == 200
        with database.get_db() as conn:
            assert conn.execute(
                "SELECT count(*) FROM chat_messages WHERE user_id = ? AND video_url = ?",
                (plain_id, url)).fetchone()[0] == 1, "追问记录被连带删了"

    def test_pending_row_can_be_deleted_and_was_never_public(self, client_app):
        """删 pending 允许，且它本来就不在对外社区列表里（前后一致）。

        pending 对外不可见是 ``_COMMUNITY_VISIBLE`` 定的：占位行里没有总结，
        当内容展示就是给用户看一个空壳。所以删它**没有对外可见后果**，
        纯粹是后台清理——库里分不清「正在解析」与「崩了的僵尸占位」，
        禁删只会让僵尸变成永远清不掉的死条目。
        """
        client, _, plain_id = client_app
        vid = _pending_video("https://v.example/pending", plain_id)
        assert client.get("/api/community/videos").json()["items"] == [], (
            "前置条件变了：pending 现在对外可见了")

        r = client.delete(f"/api/admin/community/{vid}",
                          headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": vid}

        ids = [i["id"] for i in client.get(
            "/api/admin/community", headers=_admin_hdr(client_app)).json()["items"]]
        assert vid not in ids
        assert client.get("/api/community/videos").json()["items"] == [], "对外可见性变了"

    def test_unknown_video_is_404(self, client_app):
        client, _, _ = client_app
        r = client.delete("/api/admin/community/999999",
                          headers=_admin_hdr(client_app))
        assert r.status_code == 404, r.text

    def test_requires_admin(self, client_app):
        """正反对照；且断言数据没被删。"""
        client, _, plain_id = client_app
        vid = _ready_video("https://v.example/guard", plain_id)

        no = client.delete(f"/api/admin/community/{vid}",
                           headers=_plain_hdr(client_app))
        anon = client.delete(f"/api/admin/community/{vid}")
        ok = client.delete(f"/api/admin/community/{vid}",
                           headers=_admin_hdr(client_app))
        assert (no.status_code, anon.status_code, ok.status_code) == (403, 401, 200)
        with database.get_db() as conn:
            still_there = conn.execute(
                "SELECT count(*) FROM videos WHERE id = ?", (vid,)).fetchone()[0]
        assert still_there == 0, "只有 200 那次该删掉；403/401 那两次删了就是鉴权失效"


# ── 列表：status ──────────────────────────────────────────────

class TestCommunityListExposesStatus:
    def test_every_item_carries_a_status_from_the_known_domain(self, client_app):
        """前端要靠它把「社区内容」与「占位」分开渲染，缺字段就没法画。"""
        client, _, plain_id = client_app
        _ready_video("https://v.example/s1", plain_id)
        _pending_video("https://v.example/s2", plain_id)

        items = client.get("/api/admin/community",
                           headers=_admin_hdr(client_app)).json()["items"]
        assert len(items) == 2
        for item in items:
            assert "status" in item, f"缺 status：{sorted(item)}"
            assert item["status"] in {"ready", "pending"}, item["status"]
        assert {i["video_url"]: i["status"] for i in items} == {
            "https://v.example/s1": "ready",
            "https://v.example/s2": "pending",
        }


# ── 标签词表（前端不抄词表的那条出口）─────────────────────────

class TestTagVocabulary:
    def test_no_tag_is_missing_and_nothing_is_invented(self, client_app):
        """每个 tag 都在词表内，且**没有遗漏**。

        少一个 = 管理员在后台选不到那个分类；多一个 = 后台能选出模型永远
        不会产生的标签。两种都是漂移。
        """
        client, _, _ = client_app
        r = client.get("/api/admin/tags/vocabulary", headers=_admin_hdr(client_app))
        assert r.status_code == 200, r.text
        flattened = [t for g in r.json()["groups"] for t in g["tags"]]

        outside = [t for t in flattened if t not in tags.TAG_VOCABULARY]
        assert outside == [], f"词表里出现了 tags.py 没有的标签：{outside}"
        assert set(flattened) == set(tags.TAG_VOCABULARY), (
            f"与词表不一致，缺 {sorted(set(tags.TAG_VOCABULARY) - set(flattened))}、"
            f"多 {sorted(set(flattened) - set(tags.TAG_VOCABULARY))}")

    def test_max_tags_comes_from_the_constant_and_order_is_preserved(self, client_app,
                                                                     monkeypatch):
        """maxTags 不得写死；分组展平后的顺序必须严格等于 TAG_VOCABULARY。

        为什么还要 monkeypatch 一次：MAX_TAGS 现在的值**恰好就是 3**，
        所以 ``maxTags == tags.MAX_TAGS`` 在今天对「写死 3」和「读常量」
        是同一个结果——这条断言今天是恒真的，只有等 MAX_TAGS 改成 4 才会
        咬人，而那一天没人会记得回来跑它。把常量临时抬到 4 再看一眼响应，
        就把「读常量」与「写死字面量」区分开了：端点在**调用时**读模块
        全局，所以改常量响应就跟着变。
        """
        client, _, _ = client_app
        body = client.get("/api/admin/tags/vocabulary",
                          headers=_admin_hdr(client_app)).json()

        assert body["maxTags"] == tags.MAX_TAGS, (
            f"maxTags 与 MAX_TAGS（{tags.MAX_TAGS}）对不上")
        assert set(body) == {"maxTags", "groups"}, (
            f"返回体的键集合应当恰好是 {{maxTags, groups}}，实得 {sorted(body)}"
            " —— 多出一个 max_tags 之类时，前端读错键名仍然「拿得到值」，"
            "工单 #41 那个 bug 就重新变成静默的")
        assert all(g["name"] for g in body["groups"]), "分组名不能为空"
        assert [t for g in body["groups"] for t in g["tags"]] == list(tags.TAG_VOCABULARY), (
            "分组展平后的顺序不等于 TAG_VOCABULARY：分类页与筛选器的稳定"
            "输出顺序被破坏了，展示会抖")

        monkeypatch.setattr(admin_api, "MAX_TAGS", tags.MAX_TAGS + 1)
        raised = client.get("/api/admin/tags/vocabulary",
                            headers=_admin_hdr(client_app)).json()
        assert raised["maxTags"] == tags.MAX_TAGS + 1, (
            "常量改了 maxTags 不跟着变 —— 它被写死成了字面量，"
            "词表上限一改这里就成了第二个真值来源")

    def test_requires_admin(self, client_app):
        """正反对照：只断 403 的话「全员 403」也能让它绿。"""
        client, _, _ = client_app
        ok = client.get("/api/admin/tags/vocabulary", headers=_admin_hdr(client_app))
        no = client.get("/api/admin/tags/vocabulary", headers=_plain_hdr(client_app))
        anon = client.get("/api/admin/tags/vocabulary")
        assert ok.status_code == 200
        assert ok.json()["groups"], "管理员这一侧必须真的拿到词表"
        assert (no.status_code, anon.status_code) == (403, 401)
