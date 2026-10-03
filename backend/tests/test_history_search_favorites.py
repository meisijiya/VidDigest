"""解析历史 · 搜索 / 筛选 / 收藏 / 1000 条上限。

历史页原来只有「最近 30 条 + 清空全部」。这一轮给它加的东西，每一条都对应
一个**用户会怎么骂**的场景，所以判据都落在真实的读出口上：

* ``GET /api/history``（分页 + 关键词 / 标签 / 仅收藏 / AI 状态）
* ``GET /api/history/facets``（标签筛选的选项）
* ``PATCH /api/history/{id}/favorite``（收藏切换）
* ``DELETE /api/history/{id}``（收藏项要 force）
* ``DELETE /api/history``（清空，默认保住收藏）

按测试策略的分层，这一层全是 **HTTP 层集成测试**（真实 app + 真实 TestClient
+ 真实鉴权），不另写一层的单元测试：这些判据要的正是「服务端的组合语义」，
直接调内部函数测不到「某个组合条件下服务端真的会这么返回」。

三处最容易写成「看起来实现了」的地方，刻意做成能变红：

1. **facets 的路由顺序**。``/facets`` 与 ``/{history_id}`` 都是单段路径，
   而 ``history_id`` 是 int —— 顺序反了 "facets" 会被当路径参数解析成 422，
   而 422 在界面上读起来像「这个功能没做」。所以有一条专门断它不是 422。
2. **删除双重保险必须在服务端**。做成前端两次 confirm 的话，任何忘了带
   confirm 的新入口都能删掉收藏，用户标了星的东西靠界面自觉保护等于没保护。
3. **AI 三档必须互斥且穷尽**。界面上是「全部 / 仅解析 / AI 解析」三个档，
   有一条记录落进两档或全都落不进，数字就加不起来，而用户无从判断。
"""
import pathlib
import re

import pytest

import auth
import database
import main as main_module
from database import MAX_PARSE_HISTORY_PER_USER
from seams import auth_headers, close_all_thread_connections, make_client

URL = "https://www.bilibili.com/video/BV1TEST"


# ── 造数据 ────────────────────────────────────────────────

def _history(uid, url, title="", summary=""):
    return database.upsert_parse_history(
        uid, url, video_title=title, summary_md=summary,
    )


def _community(url, tags, summary="总结"):
    """在社区视频表里放一行 ready 的内容，标签取自固定词表。"""
    database.reserve_video(url, None)
    database.complete_video(url, summary_md=summary, tags=list(tags))
    return url


def _chat(uid, url):
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages (user_id, video_url, role, content)"
            " VALUES (?, ?, 'user', 'q')", (uid, url),
        )


@pytest.fixture()
def client():
    # 不用 `with client`：lifespan 会跑 init_db()，把 db 夹具的隔离搅乱。
    return make_client(main_module.app)


def _hdr(uid, email):
    return auth_headers(auth.create_token(uid, email))


# ── 上限 ──────────────────────────────────────────────────

class TestTheCap:
    def test_the_cap_is_a_thousand(self):
        assert MAX_PARSE_HISTORY_PER_USER == 1000

    def test_the_list_is_paged_rather_than_dumping_the_whole_cap(self,
                                                                 db, client,
                                                                 make_user):
        """1000 是**保留**条数，不是单页条数。

        一次把 1000 条塞进响应体，浏览器会先卡住再把 DOM 撑爆 ——
        「保留更多」不等于「一次全给你看」。
        """
        uid = make_user("a@x.com")
        body = client.get("/api/history",
                          headers=_hdr(uid, "a@x.com")).json()
        assert "total_pages" in body and "page" in body, "没有分页信封"
        assert body["page_size"] <= 100

    def test_the_list_needs_login(self, db, client):
        """别人的历史不该被匿名读走 —— 分页信封是新的读出口。

        这里刻意**不**建任何用户：匿名请求在鉴权依赖就该被挡住，
        先建一个用户再匿名读，测到的就不是鉴权了。
        """
        assert client.get("/api/history").status_code == 401

    def test_the_endpoint_default_page_size_is_the_shared_constant(self):
        """端点的 page_size 默认值必须取自共享常量，不能写字面量。

        上一条判据（``page_size <= 100``）看不见这一层：``_clamp_page`` 会把
        page_size 夹到 COMMUNITY_PAGE_SIZE_MAX，所以默认值偷偷从 20 变成
        1000 之后，那条依然全绿 —— 而 20 与 100 对用户是 5 倍的 DOM 差别。

        断「用的是那个常量」而不是「等于 20」：写死数字迟早会与后端那份
        分叉，而分叉之后没有任何东西会发现。
        """
        src = pathlib.Path(__file__).resolve().parent.parent / "api_history.py"
        text = src.read_text(encoding="utf-8")
        assert re.search(r"page_size:\s*int\s*=\s*HISTORY_PAGE_SIZE_DEFAULT", text), (
            "api_history 的 page_size 默认值不是共享常量 —— "
            "写死的数字会与 database 那份悄悄分叉"
        )
        assert not re.search(r"page_size:\s*int\s*=\s*\d", text), (
            "api_history 的 page_size 默认值写成了字面量"
        )


# ── 分页 ──────────────────────────────────────────────────

class TestPagination:
    def test_pages_do_not_overlap_or_drop_rows(self, db, client, make_user):
        uid = make_user("a@x.com")
        for i in range(25):
            _history(uid, f"{URL}/p{i}", title=f"标题{i}")

        seen = []
        for page in (1, 2, 3):
            body = client.get("/api/history",
                              params={"page": page, "page_size": 10},
                              headers=_hdr(uid, "a@x.com")).json()
            assert body["total"] == 25
            assert body["total_pages"] == 3
            seen.extend(i["id"] for i in body["items"])

        assert len(seen) == 25, f"翻完三页只拿到 {len(seen)} 条"
        assert len(set(seen)) == 25, "分页之间有重复行"

    def test_out_of_range_page_is_empty_not_an_error(self, db, client,
                                                      make_user):
        uid = make_user("a@x.com")
        _history(uid, f"{URL}/x")
        r = client.get("/api/history", params={"page": 99},
                       headers=_hdr(uid, "a@x.com"))
        assert r.status_code == 200
        assert r.json()["items"] == []


# ── 关键词搜索 ────────────────────────────────────────────

class TestKeywordSearch:
    def test_finds_by_title(self, db, client, make_user):
        uid = make_user("a@x.com")
        _history(uid, f"{URL}/1", title="秋促狂欢开始")
        _history(uid, f"{URL}/2", title="完全无关的另一条")

        body = client.get("/api/history", params={"q": "秋促"},
                          headers=_hdr(uid, "a@x.com")).json()
        assert [i["video_title"] for i in body["items"]] == ["秋促狂欢开始"]

    def test_finds_by_url_substring(self, db, client, make_user):
        uid = make_user("a@x.com")
        _history(uid, "https://example.com/keyword-hit")
        _history(uid, "https://example.com/nope")

        body = client.get("/api/history", params={"q": "keyword"},
                          headers=_hdr(uid, "a@x.com")).json()
        assert len(body["items"]) == 1

    def test_a_pasted_link_matches_exactly_not_as_a_substring(self, db, client,
                                                              make_user):
        """粘贴链接要的是「就是这一条」。

        用 LIKE 会把「链接恰好是它前缀」的另一条也带出来，而那在界面上
        读起来是「搜到了错误的东西」。
        """
        uid = make_user("a@x.com")
        _history(uid, "https://example.com/abc")
        _history(uid, "https://example.com/abc-def")   # 前缀相同

        body = client.get("/api/history",
                          params={"q": "https://example.com/abc"},
                          headers=_hdr(uid, "a@x.com")).json()
        assert [i["video_url"] for i in body["items"]] == ["https://example.com/abc"]

    def test_search_never_reaches_another_user(self, db, client, make_user):
        a = make_user("a@x.com")
        b = make_user("b@x.com")
        _history(a, f"{URL}/secret", title="只有 A 能搜到的秘密标题")

        body = client.get("/api/history", params={"q": "秘密"},
                          headers=_hdr(b, "b@x.com")).json()
        assert body["items"] == [] and body["total"] == 0


# ── 标签筛选 ──────────────────────────────────────────────

class TestTagFilter:
    def test_filters_by_a_community_tag(self, db, client, make_user):
        uid = make_user("a@x.com")
        _community(f"{URL}/t1", ["编程"])
        _community(f"{URL}/t2", ["其他"])
        _history(uid, f"{URL}/t1", title="带编程标签")
        _history(uid, f"{URL}/t2", title="只有其他标签")

        body = client.get("/api/history", params={"tag": "编程"},
                          headers=_hdr(uid, "a@x.com")).json()
        assert [i["video_title"] for i in body["items"]] == ["带编程标签"]

    def test_a_history_row_without_a_community_row_stays_findable(self, db,
                                                                  client,
                                                                  make_user):
        """历史可以没有社区行（只解析过、没进社区）。

        用了 LEFT JOIN，但筛选条件一旦写成 INNER，这类记录在带标签查询时
        会整个消失 —— 而它本来就不该出现在某个标签下，也不能因此查不出来。
        """
        uid = make_user("a@x.com")
        _history(uid, f"{URL}/orphan", title="没有社区行的历史")

        body = client.get("/api/history", params={"q": "社区行"},
                          headers=_hdr(uid, "a@x.com")).json()
        assert len(body["items"]) == 1

    def test_malformed_tags_do_not_500_the_whole_list(self, db, client,
                                                     make_user):
        """videos.tags 是 JSON 文本，坏一行就让 json_each 抛。

        一条坏数据足以让整个历史列表 500 —— 而历史页是用户回看自己解析
        记录的地方，500 的代价比社区列表还大。
        """
        uid = make_user("a@x.com")
        _community(f"{URL}/bad", ["编程"])
        with database.get_db() as conn:
            conn.execute("UPDATE videos SET tags = '{not json' WHERE video_url = ?",
                         (f"{URL}/bad",))
        _history(uid, f"{URL}/bad", title="标签是坏的")

        # **必须带 tag 去查。** json_valid 守卫只在真的按标签筛选、
        # 也就是 json_each 真的被调用的时候才有机会生效；不传 tag 的话
        # 那条坏数据根本没人读，断言等于恒真（实测：守卫被删掉，这条
        # 照样绿）。
        r = client.get("/api/history", params={"tag": "编程"},
                       headers=_hdr(uid, "a@x.com"))
        assert r.status_code == 200, f"一行坏 tags 让整个历史列表 500 了：{r.text}"

        # 坏行不该被当成「有这个标签」，也不该把整页带走
        assert r.json()["items"] == []

    def test_facets_route_is_not_swallowed_by_the_id_route(self, db, client,
                                                           make_user):
        """``/facets`` 与 ``/{history_id}`` 都是单段路径，history_id 是 int。

        顺序反了 "facets" 会被当路径参数解析成 422，而 422 在界面上读起来
        像「这个筛选根本没做」。所以专门断它不是 422、且真的给出数据。
        """
        uid = make_user("a@x.com")
        _community(f"{URL}/f1", ["编程"])
        _history(uid, f"{URL}/f1")

        r = client.get("/api/history/facets", headers=_hdr(uid, "a@x.com"))
        assert r.status_code == 200, f"/facets 变成了 {r.status_code}"
        items = r.json()["items"]
        assert any(f["tag"] == "编程" and f["count"] >= 1 for f in items)

    def test_facets_are_not_narrowed_by_the_current_filters(self, db, client,
                                                            make_user):
        """选了标签 A 之后，标签 B 还得在列表里。

        前端从「当前页」汇总标签的话，一筛选 B 就消失了，看起来像筛选坏了。
        """
        uid = make_user("a@x.com")
        _community(f"{URL}/a", ["编程"])
        _community(f"{URL}/b", ["架构设计"])
        _history(uid, f"{URL}/a")
        _history(uid, f"{URL}/b")

        r = client.get("/api/history/facets", params={"tag": "编程"},
                       headers=_hdr(uid, "a@x.com"))
        tags = {f["tag"] for f in r.json()["items"]}
        assert "编程" in tags and "架构设计" in tags


# ── AI 状态三档 ───────────────────────────────────────────

class TestAiBuckets:
    @pytest.fixture()
    def seeded(self, db, client, make_user):
        uid = make_user("a@x.com")
        _history(uid, f"{URL}/plain", title="只解析了")
        _history(uid, f"{URL}/summary", title="有总结", summary="# 总结")
        _chat(uid, f"{URL}/chat")
        _history(uid, f"{URL}/chat", title="有问答")
        return uid

    def _titles(self, client, uid, **params):
        body = client.get("/api/history", params=params,
                          headers=_hdr(uid, "a@x.com")).json()
        return sorted(i["video_title"] for i in body["items"])

    def test_all_sees_everything(self, client, seeded):
        assert len(self._titles(client, seeded)) == 3

    def test_parse_only_sees_rows_without_ai_results(self, client, seeded):
        assert self._titles(client, seeded, ai="parse") == ["只解析了"]

    def test_ai_sees_rows_with_summary_or_chat(self, client, seeded):
        assert self._titles(client, seeded, ai="ai") == ["有总结", "有问答"]

    def test_the_two_buckets_partition_the_whole_list(self, client, seeded):
        """两档之和必须等于全部 —— 否则界面上的数字对不上。

        「一条记录落进两档」和「哪条都不落进」在 UI 上完全一样：
        筛选结果比预期多或少，而用户没有任何线索去找原因。
        """
        only = self._titles(client, seeded, ai="parse")
        withai = self._titles(client, seeded, ai="ai")
        every = self._titles(client, seeded)
        assert sorted(only + withai) == every
        assert not set(only) & set(withai)


# ── 收藏 ──────────────────────────────────────────────────

class TestFavourites:
    def test_setting_is_idempotent_not_a_toggle(self, db, client, make_user):
        """接口是「设为收藏」而不是「翻转」。

        翻转在网络重试下会把结果反过来：用户点一下收藏成功，重试一次
        就变成取消了。幂等设置天然免疫。
        """
        uid = make_user("a@x.com")
        hid = _history(uid, f"{URL}/f")
        h = _hdr(uid, "a@x.com")

        for _ in range(2):
            r = client.patch(f"/api/history/{hid}/favorite",
                             json={"is_favorite": True}, headers=h)
            assert r.status_code == 200
        assert r.json()["is_favorite"] is True

        body = client.get("/api/history", headers=h).json()
        assert body["items"][0]["is_favorite"] is True

    def test_can_un_favourite(self, db, client, make_user):
        uid = make_user("a@x.com")
        hid = _history(uid, f"{URL}/f")
        h = _hdr(uid, "a@x.com")
        client.patch(f"/api/history/{hid}/favorite", json={"is_favorite": True},
                     headers=h)
        r = client.patch(f"/api/history/{hid}/favorite",
                         json={"is_favorite": False}, headers=h)
        assert r.json()["is_favorite"] is False

    def test_favourite_filter_selects_only_starred(self, db, client, make_user):
        uid = make_user("a@x.com")
        a = _history(uid, f"{URL}/a", title="收藏的")
        _history(uid, f"{URL}/b", title="没收藏的")
        client.patch(f"/api/history/{a}/favorite", json={"is_favorite": True},
                     headers=_hdr(uid, "a@x.com"))

        body = client.get("/api/history", params={"favorite": True},
                          headers=_hdr(uid, "a@x.com")).json()
        assert [i["video_title"] for i in body["items"]] == ["收藏的"]

    def test_one_user_cannot_favourise_another_users_row(self, db, client,
                                                          make_user):
        a = make_user("a@x.com")
        b = make_user("b@x.com")
        hid = _history(a, f"{URL}/a")
        r = client.patch(f"/api/history/{hid}/favorite",
                         json={"is_favorite": True}, headers=_hdr(b, "b@x.com"))
        assert r.status_code == 404

    def test_favouriting_does_not_move_the_record_in_time(self, db, client,
                                                          make_user):
        """打星不该改 updated_at。

        列表按 updated_at 倒序：顺手刷新时间戳的话，用户点一下星标这条
        记录就会从列表中间跳到最顶上 —— 在他手指底下重排，像页面在抽。
        """
        uid = make_user("a@x.com")
        first = _history(uid, f"{URL}/1", title="第一条")
        _history(uid, f"{URL}/2", title="第二条")
        h = _hdr(uid, "a@x.com")
        before = [i["id"] for i in client.get("/api/history", headers=h).json()["items"]]

        client.patch(f"/api/history/{first}/favorite", json={"is_favorite": True},
                     headers=h)

        after = [i["id"] for i in client.get("/api/history", headers=h).json()["items"]]
        assert after == before, "收藏之后列表顺序变了"


# ── 删除双重保险 ──────────────────────────────────────────

class TestDeleteDoubleLock:
    def test_a_favourited_row_needs_force(self, db, client, make_user):
        """双重保险在**服务端**，不是前端弹两次确认。

        前端的 confirm 拦不住任何一个忘了带它的入口（脚本、curl、
        以后新加的批量操作）。用户标了星的东西靠界面自觉保护等于没保护。
        """
        uid = make_user("a@x.com")
        hid = _history(uid, f"{URL}/f")
        h = _hdr(uid, "a@x.com")
        client.patch(f"/api/history/{hid}/favorite", json={"is_favorite": True},
                     headers=h)

        refused = client.delete(f"/api/history/{hid}", headers=h)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "favorited"
        # 关键：拒了之后它还在
        assert client.get("/api/history", headers=h).json()["total"] == 1

        forced = client.delete(f"/api/history/{hid}", params={"force": True},
                               headers=h)
        assert forced.status_code == 200
        assert client.get("/api/history", headers=h).json()["total"] == 0

    def test_an_ordinary_row_deletes_without_force(self, db, client, make_user):
        uid = make_user("a@x.com")
        hid = _history(uid, f"{URL}/f")
        r = client.delete(f"/api/history/{hid}", headers=_hdr(uid, "a@x.com"))
        assert r.status_code == 200

    def test_missing_row_is_404_not_409(self, db, client, make_user):
        """不存在的行不能报 409。

        报 409 会让界面弹「这是收藏，要再确认一次吗？」，而那条记录根本
        不存在 —— 双重保险自己变成了误导。
        """
        uid = make_user("a@x.com")
        r = client.delete("/api/history/999999", headers=_hdr(uid, "a@x.com"))
        assert r.status_code == 404

    def test_cannot_delete_another_users_row(self, db, client, make_user):
        a = make_user("a@x.com")
        b = make_user("b@x.com")
        hid = _history(a, f"{URL}/a")
        r = client.delete(f"/api/history/{hid}", params={"force": True},
                          headers=_hdr(b, "b@x.com"))
        assert r.status_code == 404


class TestClearAll:
    def test_clear_all_keeps_favourites_by_default(self, db, client, make_user):
        uid = make_user("a@x.com")
        keep = _history(uid, f"{URL}/keep", title="收藏的")
        _history(uid, f"{URL}/drop1")
        _history(uid, f"{URL}/drop2")
        h = _hdr(uid, "a@x.com")
        client.patch(f"/api/history/{keep}/favorite", json={"is_favorite": True},
                     headers=h)

        r = client.delete("/api/history", headers=h)
        assert r.status_code == 200
        body = client.get("/api/history", headers=h).json()
        assert [i["video_title"] for i in body["items"]] == ["收藏的"]

    def test_force_also_clears_favourites(self, db, client, make_user):
        uid = make_user("a@x.com")
        keep = _history(uid, f"{URL}/keep")
        h = _hdr(uid, "a@x.com")
        client.patch(f"/api/history/{keep}/favorite", json={"is_favorite": True},
                     headers=h)

        r = client.delete("/api/history", params={"force": True}, headers=h)
        assert r.status_code == 200
        assert client.get("/api/history", headers=h).json()["total"] == 0


# ── 老库升级 ──────────────────────────────────────────────
#
# 单独一节的原因：上面每一条都跑在 ``db`` 夹具上，而夹具每次建**全新**的库
# 文件。全新库里 ``CREATE TABLE`` 自带 is_favorite，所以「老库没这一列、
# 靠迁移补上」那条路径**一次都没被执行过**。
#
# 于是会有这种局面：30 条全绿、真库一升级就打不开 ——
# ``CREATE INDEX ... ON parse_history(is_favorite)`` 在 executescript 里，
# 排在补列迁移之前，老库直接 no such column，整段脚本 abort，迁移永远轮不到。
# 新装环境永远看不到它，所以判据只能落在「从一个真的没有该列的库启动」上。


def _seed_legacy_database() -> int:
    """在 legacy_db 夹具指向的库里，摆出「上一版」的表结构与一行老数据。

    路径替换与连接清理由夹具负责（见 conftest 的 legacy_db）——它和 db 夹具
    的隔离强度一样，区别只是不替我们建表，好让我们先摆一张缺列的表。
    """
    database.init_db()  # 先要一份**真实**的现行 DDL，再从里面摘掉新列
    with database.get_db() as c:
        ddl = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='parse_history'"
        ).fetchone()["sql"]
        uid = c.execute(
            "INSERT INTO users (email, password_hash) VALUES ('old@x.com', 'h')"
        ).lastrowid
        legacy_ddl_lines = [ln for ln in ddl.splitlines() if "is_favorite" not in ln]
        assert len(legacy_ddl_lines) < len(ddl.splitlines()), (
            "现行 DDL 里已经找不到 is_favorite 了 —— 这条测试失去意义，"
            "请连同注释一起更新"
        )
        c.execute("DROP TABLE parse_history")  # 连同它自己的索引一起掉
        c.execute("\n".join(legacy_ddl_lines))
        c.execute(
            "INSERT INTO parse_history (user_id, video_url, video_title)"
            " VALUES (?, ?, ?)",
            (uid, "https://v.example/legacy", "升级前就有的记录"),
        )
    close_all_thread_connections()
    return uid


def test_init_db_starts_on_a_legacy_database(legacy_db):
    """从一个没有 is_favorite 的老库启动：不能炸，且老数据要留住。"""
    uid = _seed_legacy_database()

    # 这一行以前直接抛 sqlite3.OperationalError: no such column: is_favorite
    database.init_db()

    with database.get_db() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(parse_history)")}
        assert "is_favorite" in cols, "补列没有发生"
        indexes = {
            r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
                " AND tbl_name='parse_history'"
            )
        }
        assert "idx_history_fav" in indexes, "「仅收藏」的索引没建起来"
        rows = c.execute(
            "SELECT video_title, is_favorite FROM parse_history WHERE user_id=?", (uid,)
        ).fetchall()
        assert len(rows) == 1, "升级把老数据弄丢了"
        assert rows[0]["video_title"] == "升级前就有的记录"
        assert rows[0]["is_favorite"] == 0, "老记录必须升级成未收藏，而不是 NULL"


def test_favourites_work_on_a_upgraded_database(legacy_db):
    """补上列还不够：老库升级后收藏这套要真的能用。"""
    uid = _seed_legacy_database()
    database.init_db()

    with database.get_db() as c:
        hid = c.execute(
            "SELECT id FROM parse_history WHERE user_id=?", (uid,)
        ).fetchone()["id"]

    assert database.set_parse_history_favorite(uid, hid, True) is True
    assert database.get_parse_history_favorite(uid, hid) == 1
    assert database.set_parse_history_favorite(uid, hid, False) is True
    assert database.get_parse_history_favorite(uid, hid) == 0
