"""社区卡片**原样回显** `video_url`，包括查询串（工单 #10 第 1 项）。

## 这条测试是在钉现状，不是在赞现状

一个带 `?sig=...&token=...&exp=...` 的临时分享链接，解析后会变成对
**未登录访客公开**的字符串——实测种一条带查询串的视频，未登录响应里原样出现。
访客不需要任何凭据就能拿到那个签名。

修它要引入 URL 规范化，而唯一性语义是工单 #6 定下的承诺（「同一链接全站只解析
一次」靠 `idx_videos_url` 唯一索引落地），所以在设计出来之前先把**现状**钉死：
这样将来任何规范化改动都必须先让这条转红，而不是悄悄把签名从公开响应里带走。

`_project_video`（database.py）是纯透传 `{name: row[name]}`，`video_url` 不在
清洗名单里，所以这里断言的是「与库中原文逐字节相同」，而不是「包含某个子串」。

## 公开面其实只有**一个**出口

`api_community.py` 里会把 `video_url` 发出去的出口有四个，但只有
`GET /videos` 不挂鉴权依赖：

| 出口 | 鉴权 | 对访客暴露签名？ |
|---|---|---|
| `GET /videos` | 无依赖 | **是** ← 唯一的公开面 |
| `GET /search` | `get_current_user` | 否 |
| `GET /videos/by-url` | `get_current_user` | 否 |
| `GET /videos/{id}` | `get_current_user` | 否 |

所以泄漏面比「社区会公开回显」要窄得多：下面把 `/search` 的 401 钉住，
免得将来有人把它当成第二个公开出口而无人察觉。

## 断言只读外部可观察的出口

走真实 HTTP 层（`seams.make_client`），不直接调路由函数：直接调时拿到的
`user` 是依赖对象本身，鉴权根本没被执行，「测匿名」就只是手写了依赖返回值。

本文件第一版就是因为靠读代码判断「搜索大概也是公开的」而写错了——真跑一次
才发现是 401。**这类事只能实测。**

断言用**字节级相等**而不是 `in`：URL 少一个字符、顺序变了、某个参数被悄悄摘掉，
`in` 全都抓不住，而那正是规范化最容易顺手做掉的改动。
"""
import pytest

import auth
import database
from seams import auth_headers, make_client

#: 一条**真的**可能带签名参数的分享链接形态。查询串里塞的是不可再生的哨兵，
#: 出现即泄漏；断言用的是整串相等，所以哨兵只是让泄漏在失败信息里更好认。
SIGNED_URL = (
    "https://example.com/v/private-clip"
    "?sig=deadbeefcafebabe0123456789abcdef"
    "&token=tok_9f8e7d6c5b4a3210"
    "&exp=1799999999"
)

#: 查询串里的哨兵值。逐个断言「某个键不在」抓不住泄漏——泄漏在**值**里。
SIG_SENTINEL = "deadbeefcafebabe0123456789abcdef"
TOKEN_SENTINEL = "tok_9f8e7d6c5b4a3210"


@pytest.fixture()
def client_app(db):
    """真实 app + 真实鉴权依赖，只把数据库指向临时库。"""
    import main as main_module
    return main_module.app


@pytest.fixture()
def seeded_signed_url(db):
    """按工单 #6 的真实协议造一条**已就绪**的社区视频，链接带查询串。

    刻意走 reserve → complete → publish 三步而不是直接 INSERT：
    直接插入绕过了状态机与触发器，测的就不是真实路径了。
    """
    outcome, _ = database.reserve_video(SIGNED_URL, 1)
    assert outcome == "reserved", f"前提不成立：被 reserve 判成 {outcome}"
    assert database.complete_video(
        SIGNED_URL,
        summary_md="总结", mindmap_md="# 主题", tags=["生活"], subtitle_text="字幕",
    ) == 1
    assert database.publish_video_card(SIGNED_URL, "一个标题", "") == 1
    return SIGNED_URL


def anon(app):
    return make_client(app)


def as_user(app, uid, email="u@example.com"):
    return make_client(app), auth_headers(auth.create_token(uid, email))


# ── 现状：与库中原文逐字节相同 ─────────────────────────────

class TestVideoUrlIsEchoedVerbatim:
    def test_anonymous_list_echoes_query_string_byte_for_byte(self, client_app, seeded_signed_url):
        app = client_app
        with anon(app) as c:
            body = c.get("/api/community/videos").json()

        items = [it for it in body["items"] if it["video_url"] == SIGNED_URL]
        assert len(items) == 1, f"种下的那条没出现在未登录列表里：{[i['video_url'] for i in body['items']]}"
        # 字节级相等：`in` 抓不住「少一个字符 / 顺序变了 / 某个参数被摘掉」。
        assert items[0]["video_url"] == SIGNED_URL

    def test_search_is_not_a_second_public_exit(self, client_app, seeded_signed_url):
        """搜索要登录——它**不是**第二个暴露签名给访客的出口。

        本文件第一版把这儿当成公开出口写了断言，真跑一次才发现是 401。
        把它钉住有两个用处：① 记录泄漏面只有 `/videos` 一个入口；
        ② 将来若有人把 `/search` 改成公开，这条会立刻转红，提醒他重新评估暴露面。
        """
        app = client_app
        with anon(app) as c:
            r = c.get("/api/community/search", params={"q": "一个标题"})
        assert r.status_code == 401, (
            f"未登录搜索实得 {r.status_code}，预期 401。若它变成 200，"
            f"签名就多了一个公开出口，本文件的「泄漏面只有一个入口」结论要重评。"
        )

    def test_authenticated_search_still_echoes_the_original(self, client_app, make_user, seeded_signed_url):
        """搜索虽要登录，回显的仍是原文——规范化若只改 `/videos`，这里会漏。

        两个出口共用白名单投影，但**将来未必共用**，所以两条都断。
        """
        app = client_app
        uid = make_user()
        client, headers = as_user(app, uid)
        with client as c:
            r = c.get("/api/community/search", params={"q": "一个标题"}, headers=headers)

        assert r.status_code == 200, f"登录搜索应得 200，实得 {r.status_code}"
        items = r.json()["items"]
        matched = [it for it in items if SIG_SENTINEL in it["video_url"]]
        assert matched, f"搜索结果里没有那条带签名的链接：{[i['video_url'] for i in items]}"
        assert matched[0]["video_url"] == SIGNED_URL

    def test_authenticated_sees_the_same_string_as_anonymous(self, client_app, make_user, seeded_signed_url):
        """登录与否返回**完全相同**的形状——这是 api_community.py:74 的刻意设计。

        所以「登录后才看得到签名」不是可用的缓解方案：签名对访客本来就是公开的，
        登录只多给了详情内容，不改变这一列。
        """
        app = client_app
        uid = make_user()
        with anon(app) as c:
            anon_url = c.get("/api/community/videos").json()["items"][0]["video_url"]
        client, headers = as_user(app, uid)
        with client as c:
            authed_url = c.get("/api/community/videos", headers=headers).json()["items"][0]["video_url"]

        assert anon_url == authed_url == SIGNED_URL


# ── 现状的具体形状：签名确实进了公开响应 ───────────────────
#
# 这一组不是「多测一遍」，它是给上面那组**自证**用的：如果哪天投影层开始清洗
# 查询串，上面三条会一起失败——但只有这三条能回答「到底是哪一段签名泄漏的」，
# 也就是设计规范化方案时需要先保住/先摘掉的具体位置。

class TestSignatureActuallyReachesTheWire:
    def test_signed_params_are_present_in_the_anonymous_response_body(self, client_app, seeded_signed_url):
        app = client_app
        with anon(app) as c:
            raw = c.get("/api/community/videos").text

        assert SIG_SENTINEL in raw, "签名参数没出现在响应体里——规范化若已实现，本用例应转红并被重新审视"
        assert TOKEN_SENTINEL in raw, "token 参数没出现在响应体里——同上"

    def test_db_row_still_holds_the_original_including_query_string(self, db, seeded_signed_url):
        """库这一侧不该被本次测试改动：签名是**回显**问题，不是**存储**问题。

        单独断它，是为了让将来做规范化的人一眼看出该改的是投影层而不是落库值——
        改落库会连带改掉 `idx_videos_url` 的唯一性语义（工单 #6 的承诺）。
        """
        row = database.get_video_by_url(SIGNED_URL)
        assert row is not None, "库里查不到刚种下的那条"
        assert row["video_url"] == SIGNED_URL