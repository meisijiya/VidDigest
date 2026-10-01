"""路由级：SSE 事件顺序与配额扣减时机（bug 1 + bug 2）。
用桩替掉字幕提取和 LLM —— 不联网、不花钱、可重复。
"""
import asyncio
import json

import pytest

import api_summarize
from conftest import count_of

FAR_FUTURE = "2099-01-01T00:00:00+00:00"


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
        assert count_of(uid) == 0, "无字幕却扣了额度"
        assert s.calls == 0, "无字幕却调了 LLM"
        assert db.check_summary_quota(uid) == (True, db.FREE_DAILY_SUMMARY_LIMIT)

    def test_real_summary_consumes_exactly_one(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == [
            "subtitle", "quota", "summary", "summary", "mindmap", "done",
        ]
        assert count_of(uid) == 1
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota == {"remaining": 2, "limit": 3, "unlimited": False}
        assert s.calls == 1

    def test_quota_event_arrives_before_any_token(self, db, make_user, stub):
        """额度必须早于正文下发，否则前端拿不到。"""
        stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        kinds = [e[0] for e in events]
        assert kinds.index("quota") < kinds.index("summary")

    def test_fourth_use_refused_without_llm(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user()
        for _ in range(db.FREE_DAILY_SUMMARY_LIMIT):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert "今日免费次数已用完" in events[0][1]["message"]
        assert count_of(uid) == db.FREE_DAILY_SUMMARY_LIMIT
        assert s.calls == db.FREE_DAILY_SUMMARY_LIMIT

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

    def test_chat_consumes_same_budget(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["quota", "answer", "done"]
        assert count_of(uid) == 1
        assert db.check_summary_quota(uid) == (True, 2)
        assert s.calls == 1

    def test_chat_blocked_when_budget_spent(self, db, make_user, stub):
        stub(has_subtitle=True)
        uid = make_user()
        for _ in range(db.FREE_DAILY_SUMMARY_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert events[0][1]["need_vip"] is True
        assert count_of(uid) == db.FREE_DAILY_SUMMARY_LIMIT

    def test_chat_no_subtitle_does_not_consume(self, db, make_user, stub):
        stub(has_subtitle=False)
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(subtitle=""), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]
        assert count_of(uid) == 0

    def test_chat_and_summary_share_one_budget(self, db, make_user, stub):
        """问答不能变成绕过总结限额的免费通道。"""
        stub(has_subtitle=True)
        uid = make_user()
        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert count_of(uid) == db.FREE_DAILY_SUMMARY_LIMIT
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events] == ["error"]


class TestVipBypassesBudget:
    def test_vip_unlimited_on_both_endpoints(self, db, make_user, stub):
        s = stub(has_subtitle=True)
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        for _ in range(5):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert count_of(uid) == 0
        assert db.check_summary_quota(uid) == (True, -1)

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
        assert r == {"logged_in": False, "unlimited": False,
                     "remaining": None, "limit": db.FREE_DAILY_SUMMARY_LIMIT}

    def test_free_user_untouched(self, db, make_user):
        uid = make_user()
        r = asyncio.run(self._call({"id": uid}))
        assert r["logged_in"] is True and r["remaining"] == 3 and r["unlimited"] is False
        assert count_of(uid) == 0, "只读端点不该写库"

    def test_free_user_partially_spent(self, db, make_user):
        uid = make_user()
        db.consume_summary_quota(uid)
        r = asyncio.run(self._call({"id": uid}))
        assert r["remaining"] == 2

    def test_over_limit_reports_zero(self, db, make_user):
        uid = make_user()
        for _ in range(db.FREE_DAILY_SUMMARY_LIMIT):
            db.consume_summary_quota(uid)
        r = asyncio.run(self._call({"id": uid}))
        assert r["remaining"] == 0 and r["unlimited"] is False

    def test_vip_reports_unlimited(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at=FAR_FUTURE)
        r = asyncio.run(self._call({"id": uid}))
        assert r["unlimited"] is True and r["remaining"] == -1
