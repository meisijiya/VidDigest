"""路由级：SSE 事件顺序与配额扣减时机（bug 1 + bug 2）。
用桩替掉字幕提取和 LLM —— 不联网、不花钱、可重复。

工单 #4 之后额度拆成两个计数器（解析 / 追问）。本文件原先锁定
「总结与问答共用一个额度」，那些用例已按新语义重写：
每条仍然守它最初要守的那件事（扣减时机、扣几个、拒绝时是否调模型），
只是额度种类换成了对应的那个。
断言一律读 daily_parse_count / daily_chat_count——旧的
daily_summary_count 在工单 #4 之后已无生产写入者，拿它断言等于恒真。
"""
import asyncio
import json

import pytest

import api_summarize
import database

FAR_FUTURE = "2099-01-01T00:00:00+00:00"


def parse_count_of(uid):
    with database.get_db() as c:
        row = c.execute("SELECT daily_parse_count FROM users WHERE id=?", (uid,)).fetchone()
    return row["daily_parse_count"]


def chat_count_of(uid):
    with database.get_db() as c:
        row = c.execute("SELECT daily_chat_count FROM users WHERE id=?", (uid,)).fetchone()
    return row["daily_chat_count"]


class StubExtractor:
    def __init__(self, has_subtitle):
        self.has = has_subtitle

    def extract(self, url):
        return {"has_subtitle": self.has, "full_text": "transcript" if self.has else "", "segments": []}


class StubSummarizer:
    def __init__(self):
        self.calls = 0

    def summarize_stream(self, text, language):
        self.calls += 1
        yield "tok-a"
        yield "tok-b"

    def generate_mindmap(self, text, language):
        return "# mindmap"

    def chat_stream(self, text, question):
        self.calls += 1
        yield "answer-1"


@pytest.fixture()
def stub(monkeypatch):
    """返回 set_subtitles(has_subtitle) → StubSummarizer"""
    holder = {}

    def set_subtitles(has_subtitle):
        s = StubSummarizer()
        monkeypatch.setattr(api_summarize, "_get_extractor", lambda: StubExtractor(has_subtitle))
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
        holder["s"] = s
        return s

    return set_subtitles


def collect(gen):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。
    注意 payload 在 raw_data 上 —— ServerSentEvent 是 Pydantic 模型，data 与 raw_data 互斥。"""
    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
        return out

    return asyncio.run(_run())


def summarize_req():
    return api_summarize.SummarizeRequest(url="https://example.com/v", language="zh")


def chat_req(subtitle="s"):
    return api_summarize.ChatRequest(url="u", question="q", subtitle_text=subtitle)


class TestSummarizeQuotaTiming:
    def test_no_subtitle_does_not_consume(self, db, make_user, stub):
        s = stub(has_subtitle=False)
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["subtitle", "error"], events
        assert "没有可用的字幕" in events[1][1]["message"], events
        assert parse_count_of(uid) == 0, "无字幕却扣了额度"
        assert s.calls == 0, "无字幕却调了 LLM"
        assert db.check_quota_kind(uid, "parse") == (True, db.DAILY_PARSE_LIMIT), (
            "无字幕时解析额度应原封不动"
        )

    def test_real_summary_consumes_exactly_one(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == [
            "subtitle", "quota", "summary", "summary", "mindmap", "done",
        ]
        assert parse_count_of(uid) == 1
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert quota["limit"] == database.DAILY_PARSE_LIMIT
        assert quota["unlimited"] is False
        assert s.calls == 1

    def test_quota_event_arrives_before_any_token(self, db, make_user, stub):
        """额度必须早于正文下发，否则前端拿不到。"""
        stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        kinds = [e[0] for e in events]
        assert kinds.index("quota") < kinds.index("summary")

    def test_fourth_use_refused_without_llm(self, db, make_user, stub):
        """解析额度用满后拒绝，且**不调模型**——这条守的是「拒绝时零成本」。"""
        s = stub(has_subtitle=True)
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert "解析次数已用完" in events[0][1]["message"], events[0][1]["message"]
        assert parse_count_of(uid) == database.DAILY_PARSE_LIMIT
        assert s.calls == database.DAILY_PARSE_LIMIT

    def test_anonymous_refused(self, db, stub):
        stub(has_subtitle=True)
        events = collect(api_summarize.summarize_video(summarize_req(), user=None))
        assert [e[0] for e in events] == ["error"]
        assert events[0][1]["need_login"] is True
        assert events[0][1]["need_vip"] is False


class TestChatQuota:
    def test_chat_was_open_to_everyone(self, db, stub):
        """回归：修复前 /api/chat 没有任何配额检查。"""
        s = stub(has_subtitle=True)
        events = collect(api_summarize.chat_with_video(chat_req(), user=None))
        assert [e[0] for e in events] == ["error"]
        assert events[0][1]["need_login"] is True
        assert s.calls == 0

    def test_chat_consumes_its_own_budget(self, db, make_user, stub):
        """追问扣对话额度；事件顺序仍是 quota → answer → done。"""
        s = stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["quota", "answer", "done"]
        assert chat_count_of(uid) == 1
        assert database.check_quota_kind(uid, "chat") == (
            True, database.DAILY_CHAT_LIMIT - 1
        )
        assert s.calls == 1

    def test_chat_blocked_when_budget_spent(self, db, make_user, stub):
        """对话额度用满后拒绝追问。"""
        stub(has_subtitle=True)
        uid = make_user()
        for _ in range(database.DAILY_CHAT_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert events[0][1]["need_vip"] is True
        assert chat_count_of(uid) == database.DAILY_CHAT_LIMIT

    def test_chat_no_subtitle_does_not_consume(self, db, make_user, stub):
        stub(has_subtitle=False)
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(subtitle=""), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert chat_count_of(uid) == 0

    def test_chat_and_summary_use_separate_budgets(self, db, make_user, stub):
        """修订自原 test_chat_and_summary_share_one_budget（工单 #4 AC）。

        原用例守的是「问答不能变成绕过总结限额的免费通道」；
        额度拆开后那条担心换了个形式——两个计数器各自独立，
        任一用满都不该影响另一个。

        两个方向必须用两个用户：先把对话额度用满之后，
        同一个用户的对话额度本来就没了，再验证「解析用满不影响追问」
        只会得到一个必然的 error，测不出任何东西。
        """
        stub(has_subtitle=True)

        # 方向一：对话用满，解析照常
        a = make_user("a@example.com")
        for _ in range(database.DAILY_CHAT_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": a}))
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": a}))
        assert "error" not in [e[0] for e in events], "对话额度用满不该挡住解析"
        assert parse_count_of(a) == 1

        # 方向二：解析用满，追问照常
        b = make_user("b@example.com")
        for _ in range(database.DAILY_PARSE_LIMIT):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": b}))
        blocked = collect(api_summarize.summarize_video(summarize_req(), user={"id": b}))
        assert [e[0] for e in blocked] == ["error"], "前提：解析额度此时应已用满"

        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": b}))
        assert "error" not in [e[0] for e in events], "解析额度用满不该挡住追问"


class TestVipBypassesBudget:
    def test_vip_unlimited_on_both_endpoints(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        for _ in range(5):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert parse_count_of(uid) == 0, "VIP 总结不该记进解析计数"
        assert chat_count_of(uid) == 0, "VIP 追问不该记进对话计数"
        assert db.check_quota(uid) == {"parse": (True, -1), "chat": (True, -1)}

    def test_vip_quota_event_marks_unlimited(self, db, make_user, stub):
        stub(has_subtitle=True)
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["remaining"] == -1 and quota["unlimited"] is True


class TestQuotaEndpoint:
    """GET /api/quota —— 只读，前端用来在点击前就显示剩余次数。"""

    async def _call(self, user):
        return await api_summarize.get_quota(user=user)

    def test_anonymous(self, db):
        r = asyncio.run(self._call(None))
        assert r["logged_in"] is False
        assert r["parse"] is None and r["chat"] is None

    def test_free_user_untouched(self, db, make_user):
        uid = make_user()
        r = asyncio.run(self._call({"id": uid}))
        assert r["logged_in"] is True and r["unlimited"] is False
        assert r["parse"]["remaining"] == database.DAILY_PARSE_LIMIT
        assert r["chat"]["remaining"] == database.DAILY_CHAT_LIMIT
        assert parse_count_of(uid) == 0 and chat_count_of(uid) == 0, "只读端点不该写库"

    def test_free_user_partially_spent(self, db, make_user):
        uid = make_user()
        database.consume_quota(uid, "parse")
        r = asyncio.run(self._call({"id": uid}))
        assert r["parse"]["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert r["chat"]["remaining"] == database.DAILY_CHAT_LIMIT

    def test_over_limit_reports_zero(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            database.consume_quota(uid, "parse")
        r = asyncio.run(self._call({"id": uid}))
        assert r["parse"]["remaining"] == 0 and r["unlimited"] is False

    def test_vip_reports_unlimited(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        r = asyncio.run(self._call({"id": uid}))
        assert r["unlimited"] is True and r["remaining"] == -1
