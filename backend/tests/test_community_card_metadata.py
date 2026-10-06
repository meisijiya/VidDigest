"""社区卡片的标题与封面在**占位那一刻**就落库（工单 #17 第 2 项）。

缺陷本体是一条时序错配：

    App.vue  parseVideo() 成功
              └─ publishCard() 紧跟着发出 POST /api/community/cards
                 └─ 那条 UPDATE 要求 status='ready'，
                    而这张 videos 行**此刻还不存在**——它要等用户
                    点「AI 总结」才由 reserve_video() 建出来
                      → UPDATE 匹配 0 行，前端 .catch(() => {}) 连
                        updated:0 都不看

于是首次解析者填的标题与封面永远填不进去，卡片实际由**第二个访问者**
补上（他走的是 reuse 之后的完整流程，那次 UPDATE 能匹配上）。

连带后果不是观感问题：`videos_fts` 索引 `video_title`，而绝大多数行
该字段是空串，于是社区按标题搜索基本失效。

修法：把这三件事提到占位那一刻写完，而不是等 ready 之后再回填。
`videos_fts_ai` 是 AFTER INSERT，所以占位时写进去的标题**当场**可检索。

断言一律读外部可观察的出口：`videos` 行、社区搜索结果、公开列表投影。
不测私有函数，不断言内部调用顺序。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import api_summarize
from seams import StubExtractor
from seams import StubSummarizer as SeamStubSummarizer

#: 多数用例都用这个链接：同一链接只能有一行。
URL = "https://example.com/v"
TITLE = "深入理解异步编程"
COVER = "https://img.example/cover.jpg"


# ── 小工具（与 test_community_videos 同构，刻意不共享）─────────

def collect(gen):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。"""

    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append(
                (e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw))
            )
        return out

    return asyncio.run(_run())


def wire(monkeypatch, summarizer=None, extractor=None):
    """把模型与字幕提取换成桩。不联网、不花钱、可重复。"""
    summarizer = summarizer if summarizer is not None else SeamStubSummarizer()
    extractor = extractor if extractor is not None else StubExtractor()
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: summarizer)
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: extractor)
    return summarizer, extractor


def summarize(uid, **extra):
    """按真实请求模型发起一次解析，extra 是请求字段（工单 #17 第 2 项新增两项）。"""
    return api_summarize.summarize_video(
        api_summarize.SummarizeRequest(url=URL, language="zh", **extra),
        user={"id": uid},
    )


def age_row(db, seconds: int) -> None:
    """把占位行的 updated_at 往前推，模拟「占位者已经死了这么久」。"""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db.get_db() as conn:
        conn.execute("UPDATE videos SET updated_at = ?", (old,))


# ── 占位时就带上卡片字段 ──────────────────────────────────────

class TestPlaceholderCarriesCardMetadata:
    def test_placeholder_row_already_has_title_and_cover(self, db, make_user):
        """核心判据：占位行里就有标题与封面。

        判据读的是行，不是「谁调了谁」：本文件守的是落库这件事，
        至于值是从参数直接来的还是绕了一圈塞回来的，那是实现细节。
        """
        uid = make_user("first@example.com")
        assert db.reserve_video(URL, uid, TITLE, COVER)[0] == "reserved"

        row = db.get_video_by_url(URL)
        assert row is not None, "占位行没建出来"
        assert row["status"] == "pending", "前提：此刻它还该是占位，不是完成"
        assert row["video_title"] == TITLE, (
            "占位时没写标题：首次解析者的卡片标题只能等下一个人来填"
        )
        assert row["cover_url"] == COVER, "封面同理"

    def test_absent_metadata_stays_empty_string(self, db, make_user):
        """没传就是空串，读出口不出现 None。

        对外投影把这两列直接交给前端渲染，而接管分支的判据是
        `COALESCE(col,'') = ''`——两边对「空」的理解必须同一种。
        """
        uid = make_user("first@example.com")
        db.reserve_video(URL, uid)

        row = db.get_video_by_url(URL)
        assert row["video_title"] == "", repr(row["video_title"])
        assert row["cover_url"] == "", repr(row["cover_url"])

    def test_that_title_is_searchable_once_the_row_goes_ready(self, db, make_user):
        """全链路：占位时写的标题 → ready → 社区按标题搜得到。

        这一条守的是那个连带后果。社区列表要求 status='ready'，
        所以搜索判据必须落在 ready 之后——占位期它本来就不可见，
        那不是缺陷，是「不把空壳当内容展示」。
        """
        uid = make_user("first@example.com")
        db.reserve_video(URL, uid, TITLE, COVER)
        db.complete_video(URL, summary_md="一份总结")

        found = db.search_community_videos("异步编程")
        assert [item["video_url"] for item in found["items"]] == [URL], (
            "占位时写进去的标题没能被社区搜索命中"
        )


# ── 接管别人的占位：只填空，不覆盖 ────────────────────────────

class TestStaleTakeoverOnlyFillsBlanks:
    """TTL 接管写的是**别人**的占位行。

    覆盖在这里的后果不是「显示错」，而是先到的那个人被后到的
    悄悄改了卡片标题与封面——而他什么都没做。
    """

    def test_takeover_never_overwrites_an_existing_title(self, db, make_user):
        first = make_user("first@example.com")
        second = make_user("second@example.com")
        db.reserve_video(URL, first, TITLE, COVER)

        age_row(db, db.VIDEO_PENDING_TTL_SECONDS + 60)
        assert db.reserve_video(URL, second, "另一个标题", "https://img.example/x.jpg")[0] == "reserved"

        row = db.get_video_by_url(URL)
        assert row["video_title"] == TITLE, "接管把先到那个人的卡片标题改掉了"
        assert row["cover_url"] == COVER, "封面同理"

    def test_takeover_fills_a_blank_title(self, db, make_user):
        """反向：真的是空的就该被填上。

        这是这条修复里唯一**允许**写标题的时机——占位者死在了
        还没写标题之前，接管者手里正好有这个平台的元数据。
        """
        first = make_user("first@example.com")
        second = make_user("second@example.com")
        db.reserve_video(URL, first)  # 老前端 / 老客户端：什么都没带

        age_row(db, db.VIDEO_PENDING_TTL_SECONDS + 60)
        assert db.reserve_video(URL, second, TITLE, COVER)[0] == "reserved"

        row = db.get_video_by_url(URL)
        assert row["video_title"] == TITLE, "空着的标题没有被接管者补上"
        assert row["cover_url"] == COVER
        assert row["parsed_by"] == second, "前提：位置确实换了主人"

    def test_takeover_fills_a_null_title_too(self, db, make_user):
        """NULL 也算「空」——判据必须是 COALESCE，不是裸的 `= ''`。

        这一列建表时是 `TEXT DEFAULT ''`，**可空**：老库里就存在
        NULL 行。而 `NULL = ''` 求值是 NULL（不是 True），于是裸判据
        会让这些行永远填不上——症状与「修复没生效」完全同形。
        """
        first = make_user("first@example.com")
        second = make_user("second@example.com")
        db.reserve_video(URL, first)
        with db.get_db() as conn:
            conn.execute(
                "UPDATE videos SET video_title = NULL, cover_url = NULL WHERE video_url = ?",
                (URL,),
            )

        age_row(db, db.VIDEO_PENDING_TTL_SECONDS + 60)
        assert db.reserve_video(URL, second, TITLE, COVER)[0] == "reserved"

        row = db.get_video_by_url(URL)
        assert row["video_title"] == TITLE, "NULL 没被当成空：这条判据写的是裸等号"
        assert row["cover_url"] == COVER


# ── 请求字段真的到了行里 ──────────────────────────────────────

class TestSummarizeEndpointPlumbing:
    """从 HTTP 请求模型一路到 videos 行，中间任何一段漏掉都转红。"""

    def test_first_parse_stores_platform_metadata(self, db, make_user, monkeypatch):
        wire(monkeypatch)
        uid = make_user("first@example.com")

        collect(summarize(uid, video_title=TITLE, cover_url=COVER))

        row = db.get_video_by_url(URL)
        assert row["status"] == "ready", "前提：这次解析该成功"
        assert row["video_title"] == TITLE, (
            "请求里带了标题，落库却没有：中间有一段没透传"
        )
        assert row["cover_url"] == COVER

    def test_request_without_metadata_still_parses(self, db, make_user, monkeypatch):
        """老前端不带这两个字段：照常解析，只是卡片没有标题。

        兼容性判据。缺省值写错（比如让 Pydantic 必填）会让**所有**
        旧客户端的解析直接 422 ——而症状是「AI 解析整个坏了」，
        与真实原因离得很远。
        """
        wire(monkeypatch)
        uid = make_user("first@example.com")

        events = collect(summarize(uid))

        kinds = [kind for kind, _ in events]
        assert "error" not in kinds, kinds
        row = db.get_video_by_url(URL)
        assert row["status"] == "ready"
        assert row["video_title"] == "", repr(row["video_title"])