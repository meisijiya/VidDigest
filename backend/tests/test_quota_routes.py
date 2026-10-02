"""路由层：总结扣解析额度、追问扣对话额度、模型失败回滚（工单 #4）。

用上一轮建的三条接缝：StubSummarizer 逐方法计数、真实 HTTP 客户端、
以及数据层的临时库。
"""
import asyncio
import json

import pytest

import api_summarize
import database
from seams import StubExtractor, StubSummarizer, auth_headers, make_client


class ExplodingSummarizer(StubSummarizer):
    """在指定方法上抛异常——用来验证「模型失败不白扣」。

    逐方法计数照常，这样既能证明「确实调到了那个方法」，
    也能证明失败没有掩盖掉其他方法的调用。
    """

    def __init__(self, fail_on="summarize_stream", **kwargs):
        super().__init__(**kwargs)
        self.fail_on = fail_on

    def summarize_stream(self, text, language):
        self._calls["summarize_stream"] += 1
        if self.fail_on == "summarize_stream":
            raise RuntimeError("模型服务不可用")
        yield from self._summary_tokens

    def generate_mindmap(self, text, language):
        self._calls["generate_mindmap"] += 1
        if self.fail_on == "generate_mindmap":
            raise RuntimeError("模型服务不可用")
        return self._mindmap

    def chat_stream(self, text, question, history=None):
        self._calls["chat_stream"] += 1
        if self.fail_on == "chat_stream":
            raise RuntimeError("模型服务不可用")
        yield from self._answer_tokens


@pytest.fixture()
def wired(monkeypatch):
    """装好桩，返回 set_models(has_subtitle, summarizer=...) → (摘要桩, 提取桩)。"""

    def _wire(has_subtitle=True, summarizer=None):
        s = summarizer or StubSummarizer()
        e = StubExtractor(has_subtitle=has_subtitle)
        monkeypatch.setattr(api_summarize, "_get_extractor", lambda: e)
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
        return s, e

    return _wire


def collect(gen):
    """跑完 SSE 生成器，返回 [(event, payload)]。"""
    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
        return out

    return asyncio.run(_run())


def summarize_req(url="https://example.com/v"):
    return api_summarize.SummarizeRequest(url=url, language="zh")


def chat_req(subtitle="s"):
    return api_summarize.ChatRequest(url="u", question="q", subtitle_text=subtitle)


def _counts(uid):
    with database.get_db() as c:
        row = c.execute(
            "SELECT daily_parse_count, daily_chat_count FROM users WHERE id=?", (uid,)
        ).fetchone()
    return row["daily_parse_count"], row["daily_chat_count"]


class TestSummaryConsumesParseQuota:
    def test_summary_spends_parse_not_chat(self, db, make_user, wired):
        wired()
        uid = make_user()
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid) == (1, 0), "总结只该扣解析额度"

    def test_chat_spends_chat_not_parse(self, db, make_user, wired):
        wired()
        uid = make_user()
        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert _counts(uid) == (0, 1), "追问只该扣对话额度"

    def test_chat_quota_exhausted_but_parse_still_works(self, db, make_user, wired):
        """对话额度用完，解析照常——这是拆分的核心价值。"""
        wired()
        uid = make_user()
        for _ in range(database.DAILY_CHAT_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))

        blocked = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert [e[0] for e in blocked] == ["error"]

        ok = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert "error" not in [e[0] for e in ok], (
            "对话额度耗尽不该挡住解析"
        )

    def test_parse_quota_exhausted_but_chat_still_works(self, db, make_user, wired):
        wired()
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))

        blocked = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in blocked] == ["error"]

        ok = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert "error" not in [e[0] for e in ok], (
            "解析额度耗尽不该挡住追问"
        )

    def test_exhausted_message_names_the_right_quota(self, db, make_user, wired):
        """提示要说清是哪个额度用完了，否则用户不知道该等什么。"""
        wired()
        uid = make_user()
        for _ in range(database.DAILY_CHAT_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert "追问" in events[0][1]["message"], events[0][1]["message"]


class TestNoSubtitleStillFree:
    def test_summary_without_subtitle_spends_nothing(self, db, make_user, wired):
        wired(has_subtitle=False)
        uid = make_user()
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid) == (0, 0), "字幕提取失败不该扣任何额度"

    def test_chat_without_subtitle_spends_nothing(self, db, make_user, wired):
        wired(has_subtitle=False)
        uid = make_user()
        collect(api_summarize.chat_with_video(chat_req(subtitle=""), user={"id": uid}))
        assert _counts(uid) == (0, 0), "字幕提取失败不该扣任何额度"


class TestModelFailureRefunds:
    def test_summary_failure_refunds_parse_quota(self, db, make_user, wired):
        wired(summarizer=ExplodingSummarizer(fail_on="summarize_stream"))
        uid = make_user()
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid) == (0, 0), "模型失败不该白扣解析额度"
        assert database.check_quota_kind(uid, "parse") == (
            True, database.DAILY_PARSE_LIMIT
        )

    def test_mindmap_failure_refunds_parse_quota(self, db, make_user, wired):
        """思维导图是解析的第二步，它失败也要回滚——否则用户拿到半截内容却照扣。"""
        wired(summarizer=ExplodingSummarizer(fail_on="generate_mindmap"))
        uid = make_user()
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid) == (0, 0), "思维导图失败不该白扣解析额度"

    def test_chat_failure_refunds_chat_quota(self, db, make_user, wired):
        wired(summarizer=ExplodingSummarizer(fail_on="chat_stream"))
        uid = make_user()
        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert _counts(uid) == (0, 0), "模型失败不该白扣对话额度"

    def test_failure_does_not_touch_the_other_counter(self, db, make_user, wired):
        """追问失败只回滚对话额度，解析额度一个字节都不该动。"""
        wired(summarizer=ExplodingSummarizer(fail_on="chat_stream"))
        uid = make_user()
        collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid) == (1, 0)

        collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        assert _counts(uid) == (1, 0), (
            "追问失败后：解析计数保持 1（之前那次总结的），对话回到 0"
        )

    def test_failure_still_reports_error(self, db, make_user, wired):
        """回滚不是吞错误——用户仍要知道这次失败了。"""
        wired(summarizer=ExplodingSummarizer(fail_on="summarize_stream"))
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert [e[0] for e in events][-1] == "error", events

    def test_repeated_failures_do_not_inflate_quota(self, db, make_user, wired):
        """反复失败不该把额度越加越多——回滚的下界是 0。"""
        wired(summarizer=ExplodingSummarizer(fail_on="summarize_stream"))
        uid = make_user()
        for _ in range(5):
            collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        assert _counts(uid)[0] == 0, "反复失败把额度加成了正数"


class TestQuotaEndpoint:
    """GET /api/quota —— 前端在点击前就靠它显示两个剩余次数。"""

    def _call(self, user=None, headers=None):
        async def _run():
            return await api_summarize.get_quota(user=user)
        return asyncio.run(_run())

    def test_anonymous(self, db):
        r = self._call(None)
        assert r["logged_in"] is False
        assert r["parse"] is None and r["chat"] is None

    def test_reports_both_counters(self, db, make_user):
        uid = make_user()
        r = self._call({"id": uid})
        assert r["parse"]["remaining"] == database.DAILY_PARSE_LIMIT
        assert r["chat"]["remaining"] == database.DAILY_CHAT_LIMIT

    def test_reflects_each_counter_independently(self, db, make_user):
        uid = make_user()
        database.consume_quota(uid, "parse")
        r = self._call({"id": uid})
        assert r["parse"]["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert r["chat"]["remaining"] == database.DAILY_CHAT_LIMIT, (
            "解析用掉一次，对话额度不该跟着少"
        )

    def test_reports_exhausted(self, db, make_user):
        uid = make_user()
        for _ in range(database.DAILY_PARSE_LIMIT):
            database.consume_quota(uid, "parse")
        r = self._call({"id": uid})
        assert r["parse"]["remaining"] == 0
        assert r["parse"]["limit"] == database.DAILY_PARSE_LIMIT

    def test_vip_reports_unlimited_on_both(self, db, make_user):
        uid = make_user(is_vip=True, vip_expire_at="2099-01-01T00:00:00+00:00")
        r = self._call({"id": uid})
        assert r["parse"]["remaining"] == -1
        assert r["chat"]["remaining"] == -1

    def test_endpoint_does_not_write(self, db, make_user):
        uid = make_user()
        self._call({"id": uid})
        assert _counts(uid) == (0, 0), "只读端点不该写库"


class TestQuotaOverHttp:
    """走真实 HTTP 层——鉴权依赖必须真的被执行。"""

    @pytest.fixture()
    def app(self, db):
        import main as main_module
        return main_module.app

    def test_anonymous_sees_no_quota(self, app):
        with make_client(app) as c:
            r = c.get("/api/quota")
        assert r.status_code == 200
        assert r.json()["logged_in"] is False

    def test_logged_in_sees_both_counters(self, app, make_user):
        import auth

        uid = make_user()
        token = auth.create_token(uid, "a@example.com")
        with make_client(app) as c:
            r = c.get("/api/quota", headers=auth_headers(token))
        body = r.json()
        assert body["logged_in"] is True
        assert body["parse"]["remaining"] == database.DAILY_PARSE_LIMIT
        assert body["chat"]["remaining"] == database.DAILY_CHAT_LIMIT

    def test_summarize_requires_login(self, app):
        """未登录调总结走 SSE error 事件，不是 401——SSE 流的形状决定的。"""
        with make_client(app) as client:
            r = client.post("/api/summarize", json={"url": "u", "language": "zh"})
        assert "need_login" in r.text


class TestQuotaEventPayloadContract:
    """SSE quota 事件的字段契约。

    两条都是复审时用变异放出来过的洞：

    - 顶层 remaining/limit/unlimited 是留给旧前端的兼容别名，必须跟着
      「这次事件刚动的那类额度」走。旧写法在手写字典里写完 limit 之后又
      展开了 _quota_payload，顶层被 parse 的数字覆盖掉——追问一次，
      只认顶层字段的界面却显示解析余量纹丝不动。
    - 事件必须同时带 parse 和 chat 两个对象。两者缺失时前端会降级成顶层
      单数字，等于无声退回拆分前的样子，而当时没有任何测试会红。
    """

    def test_summary_event_top_level_reports_parse(self, db, make_user, wired):
        wired()
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert quota["limit"] == database.DAILY_PARSE_LIMIT
        assert quota["unlimited"] is False

    def test_chat_event_top_level_reports_chat(self, db, make_user, wired):
        wired()
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["remaining"] == database.DAILY_CHAT_LIMIT - 1
        assert quota["limit"] == database.DAILY_CHAT_LIMIT
        assert quota["unlimited"] is False

    def test_chat_event_top_level_is_not_the_parse_counter(self, db, make_user, wired):
        """先把解析额度用掉一次——若顶层仍跟着 parse 走，数字会露馅。"""
        wired()
        uid = make_user()
        database.consume_quota(uid, "parse")
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["remaining"] != database.DAILY_PARSE_LIMIT - 1, (
            "追问事件的顶层数字仍取自 parse 计数器"
        )
        assert quota["remaining"] == database.DAILY_CHAT_LIMIT - 1

    def test_summary_event_carries_both_counters(self, db, make_user, wired):
        wired()
        uid = make_user()
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["parse"]["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert quota["chat"]["remaining"] == database.DAILY_CHAT_LIMIT

    def test_chat_event_carries_both_counters(self, db, make_user, wired):
        """少一个对象，前端就会退回单数字——两条计数器一起消失。"""
        wired()
        uid = make_user()
        events = collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
        quota = next(e[1] for e in events if e[0] == "quota")
        assert quota["parse"]["remaining"] == database.DAILY_PARSE_LIMIT
        assert quota["chat"]["remaining"] == database.DAILY_CHAT_LIMIT - 1

    def test_both_events_agree_with_each_other(self, db, make_user, wired):
        """同一天里，两个事件报出来的同一计数器必须是同一个数。"""
        wired()
        uid = make_user()
        summary = next(
            e[1] for e in collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
            if e[0] == "quota"
        )
        chat = next(
            e[1] for e in collect(api_summarize.chat_with_video(chat_req(), user={"id": uid}))
            if e[0] == "quota"
        )
        assert summary["parse"] == chat["parse"], "两次事件的解析额度快照不一致"
        assert summary["chat"]["remaining"] - chat["chat"]["remaining"] == 1
