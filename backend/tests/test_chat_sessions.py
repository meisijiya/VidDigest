"""工单 #8：追问会话（字幕取自社区表 + 最近 3 轮 + 记录不裁剪 + 按用户隔离）。

断言只落在外部可观察的地方：SSE 事件、HTTP 响应、数据库最终状态，
以及**真正送到模型那里的入参**。私有函数不测，桩也不用来断言内部调用顺序。

「模型看到的上下文」这条承诺有两个观察点，缺一不可：
1. 路由递给 ``chat_stream`` 的 ``history`` —— 桩接住的就是模型将收到的；
2. 真实 ``VideoSummarizer.chat_stream`` 发给 client 的 ``messages`` 数组 ——
   前者可以被伪造（桩收下却不用），后者不行。

这批测试**能**抓住的：字幕来源被改回入参、最近轮数算错（第 1 轮漏在
上下文里，或多塞一轮）、记录被条数上限裁掉、查询漏掉 user_id 过滤、
记录改由前端写入、额度在失败路径上被扣。
**抓不住**的：模型本身答得好不好、字幕提取器内部的正确性、
以及社区视频表里那份字幕是不是真的属于这个视频（那是 #6 的承诺）。
"""

import asyncio
import json

import pytest

import api_summarize
import auth
import database
import summarizer
from seams import StubExtractor, StubSummarizer, auth_headers, make_client

URL = "https://example.com/v/shared"
COMMUNITY_SUBTITLE = "社区视频表里的字幕原文"
REQUEST_SUBTITLE = "前端入参里的另一份字幕"


# ── 夹具与工具 ──────────────────────────────────────────────

@pytest.fixture()
def app(db):
    """真实 app（含真实鉴权依赖），只把库指向临时库。"""
    import main as main_module
    return main_module.app


def collect(gen):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。"""
    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
        return out

    return asyncio.run(_run())


def chat_req(url=URL, question="问题", subtitle=""):
    return api_summarize.ChatRequest(url=url, question=question, subtitle_text=subtitle)


def seed_community_video(url, subtitle=COMMUNITY_SUBTITLE, user_id=None):
    """按 #6 的真实协议造一条已就绪的社区视频（走 reserve → complete）。

    刻意不直接 INSERT videos：那样测的就不是真实路径了。
    """
    outcome, _ = database.reserve_video(url, user_id)
    assert outcome == "reserved", f"前提不成立：{url} 被 reserve 判成 {outcome}"
    assert database.complete_video(url, subtitle_text=subtitle) == 1
    return url


class RecordingSummarizer(StubSummarizer):
    """逐次记下真正递进 chat_stream 的三个入参。"""

    def __init__(self):
        super().__init__()
        self.seen = []          # [(subtitle, question, history), ...]

    def chat_stream(self, text, question, history=()):
        self.seen.append((text, question, list(history)))
        # 答案里带上问题本身：断言才能说清「第几轮不见了」，
        # 而不是只看到一串分不出归属的 token
        self._answer_tokens = (f"答案::{question}",)
        yield from super().chat_stream(text, question, history)


@pytest.fixture()
def wire(monkeypatch):
    """装好桩，返回 _wire(...) → (RecordingSummarizer, StubExtractor)。"""

    def _wire(has_subtitle=True, subtitle_full="提取到的字幕"):
        s = RecordingSummarizer()
        e = StubExtractor(has_subtitle=has_subtitle, full_text=subtitle_full)
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
        monkeypatch.setattr(api_summarize, "_get_extractor", lambda: e)
        return s, e

    return _wire


def chat_count_of(uid):
    with database.get_db() as c:
        row = c.execute("SELECT daily_chat_count FROM users WHERE id=?", (uid,)).fetchone()
    return row["daily_chat_count"]


def stored_rows(user_id=None):
    """chat_messages 的原始行——数据库最终状态，不经任何聚合。"""
    sql = "SELECT user_id, video_url, role, content FROM chat_messages"
    params = []
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params.append(user_id)
    sql += " ORDER BY id ASC"
    with database.get_db() as c:
        return [dict(r) for r in c.execute(sql, params)]


def parse_history_count(uid):
    with database.get_db() as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM parse_history WHERE user_id = ?", (uid,)
        ).fetchone()["n"]


# ── AC1：字幕取自社区视频表，不触发字幕提取 ─────────────────

class TestSubtitleComesFromCommunityTable:
    """AC1：社区表里有字幕时，字幕提取桩的调用计数为 0。"""

    def test_extractor_is_never_called(self, db, make_user, wire):
        seed_community_video(URL)
        s, e = wire()
        uid = make_user()

        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert e.calls == 0, f"社区表里已有字幕，字幕提取仍被调了 {e.calls} 次"
        assert "error" not in [k for k, _ in events], events
        assert s.seen[0][0] == COMMUNITY_SUBTITLE, "送给模型的字幕不是社区表里那一份"

    def test_community_table_wins_over_the_request_field(self, db, make_user, wire):
        """入参是兼容路径，社区场景不得依赖它——前端可以自己编字幕。

        故意传一份**内容不同**的字幕：只断言「提取器没被调」证不了优先顺序，
        两份字幕都拿得到时照样可以不跑提取器。
        """
        seed_community_video(URL)
        s, e = wire()
        uid = make_user()

        collect(api_summarize.chat_with_video(
            chat_req(subtitle=REQUEST_SUBTITLE), user={"id": uid}
        ))

        assert s.seen[0][0] == COMMUNITY_SUBTITLE, (
            "字幕取自入参而不是社区表——前端能自己编一份字幕交给模型"
        )
        assert REQUEST_SUBTITLE not in s.seen[0][0]
        assert e.calls == 0

    def test_over_http(self, db, make_user, wire, app):
        """走真实鉴权与路由：这一条才验得到「前端传了也没用」。"""
        seed_community_video(URL)
        s, e = wire()
        uid = make_user()

        with make_client(app) as c:
            r = c.post(
                "/api/chat",
                json={"url": URL, "question": "这个视频在讲什么",
                      "subtitle_text": REQUEST_SUBTITLE},
                headers=auth_headers(auth.create_token(uid, "a@example.com")),
            )

        assert r.status_code == 200, r.text
        assert e.calls == 0, "字幕提取被调了——社区表里明明有字幕"
        assert s.seen[0][0] == COMMUNITY_SUBTITLE

    def test_request_field_still_works_when_video_is_not_in_community(self, db, make_user, wire):
        """票面明确要求保留的兼容路径：社区里没有这一份时仍然能用。"""
        s, e = wire()
        uid = make_user()

        collect(api_summarize.chat_with_video(
            chat_req(subtitle=REQUEST_SUBTITLE), user={"id": uid}
        ))

        assert s.seen[0][0] == REQUEST_SUBTITLE
        assert e.calls == 0, "入参已经够用，不该再跑字幕提取"

    def test_extraction_is_the_last_resort(self, db, make_user, wire):
        """社区表没有、入参也没有时才跑提取——三段顺序的最后一档。"""
        s, e = wire(subtitle_full="兜底提取到的字幕")
        uid = make_user()

        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert e.calls == 1
        assert s.seen[0][0] == "兜底提取到的字幕"

    def test_community_row_with_empty_subtitle_falls_through(self, db, make_user, wire):
        """占位行（还没解析出字幕）不能被当成「有字幕」。"""
        database.reserve_video(URL, None)
        s, e = wire(subtitle_full="提取到的字幕")
        uid = make_user()

        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert e.calls == 1, "空的社区字幕被当成了有字幕"
        assert s.seen[0][0] == "提取到的字幕"


# ── AC2：消耗对话额度，额度条实时回报 ───────────────────────

class TestChatSpendsChatQuotaAndReportsIt:
    def test_counter_moves_by_exactly_one(self, db, make_user, wire):
        seed_community_video(URL)
        wire()
        uid = make_user()
        before = database.check_quota_kind(uid, "chat")

        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert chat_count_of(uid) == 1
        assert database.check_quota_kind(uid, "chat")[1] == before[1] - 1, before

    def test_parse_counter_is_untouched(self, db, make_user, wire):
        seed_community_video(URL)
        wire()
        uid = make_user()

        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert database.check_quota_kind(uid, "parse")[1] == database.DAILY_PARSE_LIMIT

    def test_quota_event_carries_the_post_deduction_number(self, db, make_user, wire):
        seed_community_video(URL)
        wire()
        uid = make_user()

        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        quota = next(p for k, p in events if k == "quota")

        assert quota["chat"]["remaining"] == database.DAILY_CHAT_LIMIT - 1
        # 顶层是给只认单数字的旧前端留的别名，必须跟着**刚被扣的那类**额度走
        assert quota["remaining"] == database.DAILY_CHAT_LIMIT - 1, (
            "顶层数字不是刚扣掉的对话额度，界面会显示解析余量"
        )
        assert quota["unlimited"] is False

    def test_quota_event_arrives_before_the_first_token(self, db, make_user, wire):
        """事件晚于正文等于没有：额度条要在打字机开始前就更新。"""
        seed_community_video(URL)
        wire()
        uid = make_user()

        kinds = [k for k, _ in collect(
            api_summarize.chat_with_video(chat_req(), user={"id": uid})
        )]

        assert kinds.index("quota") < kinds.index("answer"), kinds


# ── AC3：最近 3 轮，第 4 轮时第 1 轮不在上下文里 ─────────────

class TestRecentTurnsReachTheModel:
    """AC3：上下文里保留最近 3 轮（含本轮），所以第 4 轮时第 1 轮已经掉出去。"""

    @staticmethod
    def _ask(summarizer, uid, turns, url=URL):
        for i in range(1, turns + 1):
            collect(api_summarize.chat_with_video(
                chat_req(url=url, question=f"第{i}轮的问题"), user={"id": uid}
            ))

    def test_round_one_text_is_gone_from_round_four(self, db, make_user, wire):
        seed_community_video(URL)
        s, _e = wire()
        uid = make_user()
        self._ask(s, uid, 4)

        contents = [c for _role, c in s.seen[3][2]]
        assert "第1轮的问题" not in contents, "第 1 轮的问题还在第 4 轮的上下文里"
        assert "答案::第1轮的问题" not in contents, "第 1 轮的回答还在第 4 轮的上下文里"

    def test_window_is_exactly_the_last_three_turns(self, db, make_user, wire):
        """集合相等，不是「某句不在」。

        少一轮（把第 2 轮也漏掉）、多一轮（第 1 轮赖着不走）、
        顺序倒过来、history 里混进本轮问题——这四种形态里只有相等能全抓住。
        """
        seed_community_video(URL)
        s, _e = wire()
        uid = make_user()
        self._ask(s, uid, 4)

        def turn(i):
            return [("user", f"第{i}轮的问题"), ("assistant", f"答案::第{i}轮的问题")]

        assert s.seen[0][2] == [], "第一次追问不该有历史"
        assert s.seen[1][2] == turn(1)
        assert s.seen[2][2] == turn(1) + turn(2)
        assert s.seen[3][2] == turn(2) + turn(3), (
            "第 4 轮看到的应该正好是第 2、3 轮加本轮"
        )

    def test_history_is_time_ordered(self, db, make_user, wire):
        """时间序给模型才读得通；倒序的「最近三轮」等于把对话念反了。"""
        seed_community_video(URL)
        s, _e = wire()
        uid = make_user()
        self._ask(s, uid, 3)

        assert [r for r, _c in s.seen[2][2]] == ["user", "assistant", "user", "assistant"]

    def test_another_video_session_does_not_leak_in(self, db, make_user, wire):
        """上下文按 (user_id, video_url) 取，别处问过的不该进来。"""
        s, _e = wire()
        uid = make_user()
        seed_community_video(URL)
        seed_community_video("https://example.com/v/other")

        collect(api_summarize.chat_with_video(
            chat_req(url="https://example.com/v/other", question="另一个视频的问题"),
            user={"id": uid},
        ))
        collect(api_summarize.chat_with_video(chat_req(question="本视频的问题"), user={"id": uid}))

        assert s.seen[1][2] == [], "别的视频的会话漏进来了"

    def test_round_one_never_reaches_the_model_client(self, monkeypatch):
        """第二个观察点：真实 chat_stream 发给 client 的 messages 里也没有它。

        上面的桩只证到「路由递进去什么」——桩完全可以接住 history 却不用。
        这里用真实的 VideoSummarizer（公开方法）+ 假 client，直接看发出去的东西。
        """
        sent = _messages_sent_to_model(
            monkeypatch,
            history=[
                ("user", "第2轮的问题"), ("assistant", "答案::第2轮的问题"),
                ("user", "第3轮的问题"), ("assistant", "答案::第3轮的问题"),
            ],
            question="第4轮的问题",
        )
        blob = json.dumps(sent, ensure_ascii=False)

        assert "第1轮的问题" not in blob, "第 1 轮的原文被发给了模型"
        assert "第2轮的问题" in blob and "第3轮的问题" in blob
        assert sent[-1] == {"role": "user", "content": "第4轮的问题"}, (
            "本轮问题必须独占最后一条"
        )

    def test_current_question_is_not_duplicated_into_history(self, monkeypatch):
        """本轮问题单独追加在最后，不能同时又出现在历史里。"""
        sent = _messages_sent_to_model(
            monkeypatch,
            history=[("user", "第1轮的问题"), ("assistant", "答案::第1轮的问题")],
            question="第2轮的问题",
        )
        user_msgs = [m["content"] for m in sent if m["role"] == "user"]

        assert user_msgs.count("第2轮的问题") == 1, user_msgs

    def test_subtitle_still_reaches_the_model(self, monkeypatch):
        """接上历史之后字幕不能被挤掉——它才是这一轮的事实来源。"""
        sent = _messages_sent_to_model(
            monkeypatch, history=[("user", "刚才那个呢")], question="再详细点",
        )
        blob = json.dumps(sent, ensure_ascii=False)

        assert "字幕原文" in blob


class _FakeDelta:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.delta = _FakeDelta(content)


class _FakeChunk:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _RecordingCompletions:
    """记下真正发给模型的那份 messages。"""

    def __init__(self, sink):
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs["messages"])
        return [_FakeChunk("好")]


def _messages_sent_to_model(monkeypatch, history, question="问题"):
    """用真实的 VideoSummarizer.chat_stream，返回发给 client 的 messages 数组。

    只把 client 换成假的：prompt 怎么拼是这一工单自己改的，
    绕开它去断言那个私有静态方法等于把真正的出口摘掉了。
    """
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("ALIYUN_BAILIAN_BASE_URL", "https://example.invalid/v1")
    sink = []

    real = summarizer.VideoSummarizer()
    real.client = type(
        "FakeClient", (), {"chat": type("FakeChat", (), {
            "completions": _RecordingCompletions(sink)})()}
    )()

    assert list(real.chat_stream("字幕原文", question, history)) == ["好"]
    assert len(sink) == 1, "模型被调了不止一次"
    return sink[0]


# ── AC4：独立会话表，不因条数上限被删除 ─────────────────────

class TestRecordsSurviveTheRowCap:
    VIDEO_COUNT = database.MAX_PARSE_HISTORY_PER_USER + 3   # 33 个不同视频

    def test_more_than_thirty_videos_keep_every_record(self, db, make_user, wire, monkeypatch):
        """同一个用户对 33 个**不同**视频各追问一次，每一条都还在。

        必须用不同视频：同一个视频追问 33 次是同一批行，`id NOT IN (...)`
        那种裁剪规则删不掉它们——那种用法证不了任何东西。

        先把 parse_history 填到上限：追问记录一旦又落回那张表，
        这 33 条里至少有 3 条会被 30 条滚动规则抹掉。
        """
        monkeypatch.setattr(database, "DAILY_CHAT_LIMIT", self.VIDEO_COUNT + 5)
        _s, _e = wire()
        uid = make_user()
        for i in range(database.MAX_PARSE_HISTORY_PER_USER):
            database.upsert_parse_history(
                user_id=uid, video_url=f"https://example.com/old/{i}"
            )

        expected = {}
        for i in range(self.VIDEO_COUNT):
            url = f"https://example.com/v/{i}"
            seed_community_video(url, user_id=uid)
            collect(api_summarize.chat_with_video(
                chat_req(url=url, question=f"第{i}个视频的问题"), user={"id": uid}
            ))
            expected[url] = [{
                "question": f"第{i}个视频的问题",
                "answer": f"答案::第{i}个视频的问题",
            }]

        # 集合相等：少一条、多一条、串到别的视频上，全都红
        actual = {url: database.get_chat_session(uid, url) for url in expected}
        assert actual == expected, (
            f"{self.VIDEO_COUNT} 条里对上了 {sum(1 for u in expected if actual[u] == expected[u])} 条"
        )
        assert len(stored_rows(uid)) == self.VIDEO_COUNT * 2, (
            "问答没成对落库，或有记录被裁掉了"
        )
        assert parse_history_count(uid) == database.MAX_PARSE_HISTORY_PER_USER, (
            "追问往那张会滚动裁剪的表里写了东西"
        )

    def test_records_survive_later_parse_activity(self, db, make_user, wire, monkeypatch):
        """追问记录不会因为用户之后又解析了别的视频而消失。

        这是票面点名的那个缺陷的原样复现：老实现把问答挂在 parse_history
        那一行上，解析记录一多，行被 30 条滚动规则删掉，问答跟着一起没了。
        """
        _s, _e = wire()
        uid = make_user()
        seed_community_video(URL, user_id=uid)
        collect(api_summarize.chat_with_video(
            chat_req(question="先问的那一次"), user={"id": uid}
        ))

        for i in range(database.MAX_PARSE_HISTORY_PER_USER + 5):
            database.upsert_parse_history(
                user_id=uid, video_url=f"https://example.com/other/{i}"
            )

        assert database.get_chat_session(uid, URL) == [
            {"question": "先问的那一次", "answer": "答案::先问的那一次"}
        ], "追问记录被后来的解析挤掉了"

    def test_a_question_without_an_answer_is_never_paired(self, db, make_user):
        """断流留下半轮记录时不能配成一个空回答——半轮对话不配对展示。"""
        uid = make_user()
        database.append_chat_turn(uid, URL, "问过", "答过")
        with database.get_db() as c:
            c.execute(
                "INSERT INTO chat_messages (user_id, video_url, role, content)"
                " VALUES (?, ?, 'user', ?)",
                (uid, URL, "断流时只有问题"),
            )

        assert database.get_chat_session(uid, URL) == [
            {"question": "问过", "answer": "答过"}
        ]


# ── AC5：按用户隔离，B 读不到 A 的记录 ─────────────────────

class TestSessionsAreIsolatedPerUser:
    def test_each_user_sees_only_their_own_turns(self, db, make_user, wire):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        seed_community_video(URL)
        s, _e = wire()

        collect(api_summarize.chat_with_video(chat_req(question="A 问的"), user={"id": a}))
        collect(api_summarize.chat_with_video(chat_req(question="B 问的"), user={"id": b}))

        assert database.get_chat_session(a, URL) == [
            {"question": "A 问的", "answer": "答案::A 问的"}
        ]
        assert database.get_chat_session(b, URL) == [
            {"question": "B 问的", "answer": "答案::B 问的"}
        ]
        # 上下文也不串：B 的第 1 轮不该带出 A 的第 1 轮
        assert s.seen[1][2] == [], f"B 的上下文里混进了 A 的记录：{s.seen[1]}"

    def test_b_cannot_open_a_history_record_over_http(self, db, make_user, wire, app):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        seed_community_video(URL)
        _s, _e = wire()
        collect(api_summarize.chat_with_video(
            chat_req(question="A 的私密问题"), user={"id": a}
        ))
        a_history = database.upsert_parse_history(user_id=a, video_url=URL)

        with make_client(app) as c:
            r = c.get(
                f"/api/history/{a_history}",
                headers=auth_headers(auth.create_token(b, "b@example.com")),
            )
        assert r.status_code == 404, f"B 读到了 A 的记录：{r.text}"

        with make_client(app) as c:
            own = c.get(
                f"/api/history/{a_history}",
                headers=auth_headers(auth.create_token(a, "a@example.com")),
            )
        assert own.status_code == 200
        assert own.json()["chat_history"] == [
            {"question": "A 的私密问题", "answer": "答案::A 的私密问题"}
        ]

    def test_b_own_detail_shows_only_b(self, db, make_user, wire, app):
        """A、B 都解析过同一个视频时，B 的详情里不能混进 A 的追问。"""
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        seed_community_video(URL)
        _s, _e = wire()
        collect(api_summarize.chat_with_video(chat_req(question="A 问的"), user={"id": a}))
        collect(api_summarize.chat_with_video(chat_req(question="B 问的"), user={"id": b}))
        b_history = database.upsert_parse_history(user_id=b, video_url=URL)

        with make_client(app) as c:
            r = c.get(
                f"/api/history/{b_history}",
                headers=auth_headers(auth.create_token(b, "b@example.com")),
            )

        assert r.status_code == 200
        assert r.json()["chat_history"] == [
            {"question": "B 问的", "answer": "答案::B 问的"}
        ]

    def test_list_flag_follows_the_session_table_per_user(self, db, make_user, wire):
        """has_chat 按 user_id 查：同一个视频，A 问过不等于 B 有记录。"""
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        seed_community_video(URL)
        _s, _e = wire()
        collect(api_summarize.chat_with_video(chat_req(question="A 问的"), user={"id": a}))
        database.upsert_parse_history(user_id=a, video_url=URL)
        database.upsert_parse_history(user_id=b, video_url=URL)

        assert [i["has_chat"] for i in database.get_parse_histories(a)] == [True]
        assert [i["has_chat"] for i in database.get_parse_histories(b)] == [False], (
            "B 的列表里出现了别人的追问记录"
        )


# ── AC6：额度耗尽时明确提示 ─────────────────────────────────

class TestExhaustedQuotaIsRefused:
    def _drain(self, uid, s, limit):
        for i in range(limit):
            collect(api_summarize.chat_with_video(
                chat_req(question=f"第{i}次"), user={"id": uid}
            ))

    def test_refusal_names_the_chat_quota_and_how_to_get_more(
        self, db, make_user, wire, monkeypatch
    ):
        monkeypatch.setattr(database, "DAILY_CHAT_LIMIT", 2)
        seed_community_video(URL)
        s, e = wire()
        uid = make_user()
        self._drain(uid, s, 2)

        events = collect(api_summarize.chat_with_video(
            chat_req(question="超额的那一次"), user={"id": uid}
        ))
        payload = next(p for k, p in events if k == "error")

        assert [k for k, _ in events] == ["error"], events
        # 可区分：说清是**追问**额度，并说清什么时候能再来
        assert "追问" in payload["message"], payload
        assert "重置" in payload["message"], payload
        # 不是那句笼统的「请先登录」——那会让用户去注册而不是等次日
        assert "登录" not in payload["message"], payload
        assert payload["need_login"] is False

    def test_refused_turn_costs_nothing_and_records_nothing(
        self, db, make_user, wire, monkeypatch
    ):
        monkeypatch.setattr(database, "DAILY_CHAT_LIMIT", 2)
        seed_community_video(URL)
        s, e = wire()
        uid = make_user()
        self._drain(uid, s, 2)

        collect(api_summarize.chat_with_video(
            chat_req(question="超额的那一次"), user={"id": uid}
        ))

        assert chat_count_of(uid) == 2, "被拒的这次扣了额度"
        assert len(s.seen) == 2, "被拒的这次调了模型"
        assert e.calls == 0, "被拒的这次跑了字幕提取"
        assert database.get_chat_session(uid, URL) == [
            {"question": "第0次", "answer": "答案::第0次"},
            {"question": "第1次", "answer": "答案::第1次"},
        ], "被拒的这次留下了记录"


# ── AC7：字幕取不到不扣额度 ─────────────────────────────────

class TestSubtitleFailureCostsNothing:
    def test_nothing_is_spent_and_nothing_is_recorded(self, db, make_user, wire):
        s, e = wire(has_subtitle=False)
        uid = make_user()
        before = database.check_quota_kind(uid, "chat")

        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        assert [k for k, _ in events] == ["error"], events
        assert chat_count_of(uid) == 0, "字幕取不到却扣了对话额度"
        assert database.check_quota_kind(uid, "chat") == before
        assert s.seen == [], "字幕取不到却调了模型"
        assert stored_rows(uid) == [], "字幕取不到却留下了追问记录"

    def test_failure_event_keeps_the_reason_shape(self, db, make_user, wire):
        """复用 #3 统一过的那套 reason/asr_reason，不另造一套文案。"""
        from summarizer import FAIL_FETCH_FAILED

        class ReasonStub:
            def extract(self, url):
                return {
                    "has_subtitle": False, "full_text": "", "segments": [],
                    "fail_reason": FAIL_FETCH_FAILED, "asr_fail_reason": "asr_failed",
                }

        wire(has_subtitle=False)
        import api_summarize as mod
        orig = mod._get_extractor
        mod._get_extractor = lambda: ReasonStub()
        try:
            uid = make_user()
            events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        finally:
            mod._get_extractor = orig

        payload = next(p for k, p in events if k == "error")
        assert {"message", "reason", "asr_reason"} <= set(payload), payload
        assert payload["reason"] == FAIL_FETCH_FAILED
        assert "无法回答问题" in payload["message"]


# ── AC8：未登录要求登录 ─────────────────────────────────────

class TestAnonymousIsAskedToLogIn:
    def test_no_model_no_record_no_login_free_pass(self, db, wire):
        s, e = wire()

        events = collect(api_summarize.chat_with_video(chat_req(), user=None))

        assert [k for k, _ in events] == ["error"], events
        assert events[0][1]["need_login"] is True
        assert s.seen == [], "未登录却调了模型"
        assert e.calls == 0
        assert stored_rows() == [], "未登录却留下了追问记录"

    def test_over_http(self, db, make_user, wire, app):
        """走真实鉴权依赖：光手写 user=None 只证明路由读了参数。"""
        seed_community_video(URL)
        s, e = wire()
        make_user("a@example.com")

        with make_client(app) as c:
            r = c.post("/api/chat", json={"url": URL, "question": "偷跑的一次"})

        assert r.status_code == 200, r.text
        assert "need_login" in r.text, r.text
        assert s.seen == [], "未登录却调了模型"
        assert e.calls == 0, "未登录却跑了字幕提取"
        assert stored_rows() == []


# ── 真相源唯一：只有服务端写记录 ───────────────────────────

class TestTheServerIsTheOnlyWriter:
    """工单 #8 的机制本体：记录由服务端在产出答案时落。

    只要还存在一个能凭空写记录的接口，用户就能伪造自己问过什么——
    追问记录是他自己的数据，它该不该存在不该由前端说了算。
    """

    def test_the_frontend_save_endpoint_no_longer_exists(self, db, make_user, app):
        uid = make_user()
        with make_client(app) as c:
            r = c.post(
                "/api/history/chat",
                json={"url": URL, "question": "伪造的一问", "answer": "伪造的一答"},
                headers=auth_headers(auth.create_token(uid, "a@example.com")),
            )

        assert r.status_code in (404, 405), (
            f"前端保存追问记录的接口还在（{r.status_code}），记录仍可被凭空伪造"
        )
        assert stored_rows(uid) == [], "伪造的追问记录被写进库了"

    def test_a_record_appears_without_any_second_request(self, db, make_user, wire):
        """一次正常追问之后记录就在了——全程没有第二个请求参与。"""
        seed_community_video(URL)
        _s, _e = wire()
        uid = make_user()

        collect(api_summarize.chat_with_video(
            chat_req(question="只有这一个请求"), user={"id": uid}
        ))

        assert database.get_chat_session(uid, URL) == [
            {"question": "只有这一个请求", "answer": "答案::只有这一个请求"}
        ]

    def test_a_disconnected_stream_records_nothing_but_refunds(self, db, make_user, wire):
        """断流：用户只拿到半截答案，那半截不该被记成「他问过并得到了回答」。

        而额度必须退回——付了钱只拿到一半是最该回滚的那种情况。
        """
        seed_community_video(URL)
        s, _e = wire()
        uid = make_user()

        async def _run():
            agen = api_summarize.chat_with_video(chat_req(question="断掉的那次"), user={"id": uid})
            async for _event in agen:
                break                      # 就这里断开
            await agen.aclose()

        asyncio.run(_run())

        assert stored_rows(uid) == [], "断流的半截答案被记成了完整一轮"
        assert chat_count_of(uid) == 0, "断流白扣了对话额度"


# ── 老数据回退 ─────────────────────────────────────────────

class TestLegacyRecordsStayReadable:
    """老库里的记录还躺在 parse_history.chat_history 上，尚未迁移。"""

    def _seed_legacy(self, uid):
        payload = json.dumps(
            [{"question": "老问题", "answer": "老回答"}], ensure_ascii=False
        )
        with database.get_db() as c:
            return c.execute(
                """INSERT INTO parse_history
                   (user_id, video_url, chat_history, created_at, updated_at)
                   VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
                (uid, URL, payload),
            ).lastrowid

    def test_old_column_is_read_when_the_session_table_is_empty(self, db, make_user):
        uid = make_user()
        history_id = self._seed_legacy(uid)

        detail = database.get_parse_history_detail(uid, history_id)
        assert detail["chat_history"] == [{"question": "老问题", "answer": "老回答"}], (
            "老数据消失了——新表为空时必须回退读旧列"
        )
        assert database.get_parse_histories(uid)[0]["has_chat"] is True, (
            "详情里读得到记录、列表里却没有，本身就自相矛盾"
        )

    def test_new_session_takes_precedence_over_the_legacy_column(self, db, make_user):
        """一旦这个视频有了新会话，就以会话表为准。

        契约明确写的是「新表为空时回退」，所以新老记录不合并。
        代价是老用户追问一次之后，迁移前的那几条在界面上不再出现——
        这条用例把那个取舍钉住，改动它等于改设计，不是改实现。
        """
        uid = make_user()
        history_id = self._seed_legacy(uid)
        database.append_chat_turn(uid, URL, "新问题", "新回答")

        detail = database.get_parse_history_detail(uid, history_id)
        assert detail["chat_history"] == [{"question": "新问题", "answer": "新回答"}]

    def test_corrupt_legacy_json_degrades_to_empty(self, db, make_user):
        """旧列是脏数据时不能让详情接口 500。"""
        uid = make_user()
        with database.get_db() as c:
            history_id = c.execute(
                """INSERT INTO parse_history
                   (user_id, video_url, chat_history, created_at, updated_at)
                   VALUES (?, ?, '{不是 JSON', datetime('now'), datetime('now'))""",
                (uid, URL),
            ).lastrowid

        detail = database.get_parse_history_detail(uid, history_id)
        assert detail["chat_history"] == []


def _key(turn):
    """一轮问答的可比较形式——集合断言要能说清「是哪一轮」。"""
    return (turn["question"], turn["answer"])


class TestChatSessionEndpoint:
    """补单：GET /api/history/chat?url= —— 追问会话的读出口。

    此前唯一的读路径 /api/history/{id} 由 parse_history 行驱动，而 B 追问
    A 解析的视频时他并没有那一行。所以「B 读不到 A 的记录」当时是**假通过**
    ——不是隔离生效，是谁的都读不到；票面「用户能看到自己与某个视频的完整
    追问记录」同样不成立。这组用例的核心判据是**读得回自己的**。
    """

    def _read(self, app, uid, url, email):
        with make_client(app) as c:
            return c.get(
                "/api/history/chat", params={"url": url},
                headers=auth_headers(auth.create_token(uid, email)),
            )

    def test_reader_reads_back_its_own_session_with_no_parse_record(
        self, db, make_user, wire, app
    ):
        """核心判据：追问 → 刷新 → 读得回。

        B 从没解析过这个视频（parse_history 行数为 0），所以这条读到的
        只能是会话表，不是碰巧命中了详情路径。
        """
        b = make_user("b@example.com")
        seed_community_video(URL)
        wire()
        collect(api_summarize.chat_with_video(chat_req(question="B 问的"), user={"id": b}))
        assert parse_history_count(b) == 0, "前提：B 没有解析记录行"

        r = self._read(app, b, URL, "b@example.com")
        assert r.status_code == 200, r.text
        assert r.json()["chat_history"] == [
            {"question": "B 问的", "answer": "答案::B 问的"}
        ], "刷新之后读不回自己刚问的"

    def test_reader_gets_its_own_rows_and_nothing_else(self, db, make_user, wire, app):
        """集合相等：A 问了两轮、B 问了一轮，B 读回来只有 B 那一轮。"""
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        seed_community_video(URL)
        wire()
        collect(api_summarize.chat_with_video(chat_req(question="A 的第一问"), user={"id": a}))
        collect(api_summarize.chat_with_video(chat_req(question="A 的第二问"), user={"id": a}))
        collect(api_summarize.chat_with_video(chat_req(question="B 问的"), user={"id": b}))

        got = self._read(app, b, URL, "b@example.com").json()["chat_history"]
        assert set(map(_key, got)) == {("B 问的", "答案::B 问的")}, got

        a_got = self._read(app, a, URL, "a@example.com").json()["chat_history"]
        assert set(map(_key, a_got)) == {
            ("A 的第一问", "答案::A 的第一问"),
            ("A 的第二问", "答案::A 的第二问"),
        }, a_got

    def test_anonymous_is_rejected(self, db, make_user, app):
        make_user("a@example.com")
        with make_client(app) as c:
            r = c.get("/api/history/chat", params={"url": URL})
        assert r.status_code == 401, r.text

    def test_other_url_returns_empty(self, db, make_user, wire, app):
        """换个 url 读必须为空——串了就是隔离没做。"""
        b = make_user("b@example.com")
        seed_community_video(URL)
        seed_community_video("https://example.com/v/other")
        wire()
        collect(api_summarize.chat_with_video(
            chat_req(question="在 URL 上问的"), user={"id": b}
        ))

        r = self._read(app, b, "https://example.com/v/other", "b@example.com")
        assert r.status_code == 200, r.text
        assert r.json()["chat_history"] == [], "别的视频上问的串过来了"

    def test_route_survives_the_int_path_parameter_next_to_it(self, db, make_user, app):
        """/chat 与 /{history_id} 共存。顺序反了是 422（被当 int 解析），不是 404。"""
        b = make_user("b@example.com")
        r = self._read(app, b, URL, "b@example.com")
        assert r.status_code == 200, (
            f"读端点被 /{{history_id}} 吃掉了（{r.status_code}）——注册顺序反了"
        )
        assert "chat_history" in r.json()

        with make_client(app) as c:
            detail = c.get("/api/history/999", headers=auth_headers(
                auth.create_token(b, "b@example.com")
            ))
        assert detail.status_code == 404, f"老路径被挤坏了：{detail.text}"

    def test_response_shape_is_the_existing_one(self, db, make_user, wire, app):
        """不新造形状：前端 chatHistoryList 吃的就是 [{question, answer}]。"""
        b = make_user("b@example.com")
        seed_community_video(URL)
        wire()
        collect(api_summarize.chat_with_video(chat_req(question="形状"), user={"id": b}))

        body = self._read(app, b, URL, "b@example.com").json()
        assert set(body) == {"chat_history"}, body
        assert [set(t) for t in body["chat_history"]] == [{"question", "answer"}], body

    def test_legacy_record_is_reachable_through_the_new_endpoint(self, db, make_user, app):
        """老列回退也归 get_chat_session 管：新读出口不能只认新表。"""
        uid = make_user()
        _seed_legacy_row(uid)

        r = self._read(app, uid, URL, "u@example.com")
        assert r.status_code == 200, r.text
        assert r.json()["chat_history"] == [{"question": "老问题", "answer": "老回答"}], (
            "新端点不认老列——老用户在这条路上看不到自己的历史"
        )

    def test_new_session_shadows_the_legacy_row(self, db, make_user, app):
        """同一套回退规则在两个读点上一致：会话表非空时以它为准。"""
        uid = make_user()
        _seed_legacy_row(uid)
        database.append_chat_turn(uid, URL, "新问题", "新回答")

        r = self._read(app, uid, URL, "u@example.com")
        assert r.json()["chat_history"] == [{"question": "新问题", "answer": "新回答"}]

    def test_legacy_fallback_does_not_become_a_hole_in_isolation(self, db, make_user, app):
        """旧列也按 user_id 过滤——回退路径不能变成绕过隔离的后门。"""
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        _seed_legacy_row(a, question="A 的老问题", answer="A 的老回答")

        r = self._read(app, b, URL, "b@example.com")
        assert r.status_code == 200, r.text
        assert r.json()["chat_history"] == [], "B 读到了 A 的旧记录"


def _seed_legacy_row(uid, question="老问题", answer="老回答"):
    """造一条只有老库形态的记录：parse_history.chat_history 有值，chat_messages 空。"""
    payload = json.dumps([{"question": question, "answer": answer}], ensure_ascii=False)
    with database.get_db() as c:
        c.execute(
            """INSERT INTO parse_history
               (user_id, video_url, chat_history, created_at, updated_at)
               VALUES (?, ?, ?, datetime('now'), datetime('now'))""",
            (uid, URL, payload),
        )
