"""路由级：SSE 事件顺序与配额扣减时机（bug 1 + bug 2）。
用桩替掉字幕提取和 LLM —— 不联网、不花钱、可重复。

工单 #4 之后额度拆成两个计数器（解析 / 追问）。本文件原先锁定
「总结与问答共用一个额度」，那些用例已按新语义重写：
每条仍然守它最初要守的那件事（扣减时机、扣几个、拒绝时是否调模型），
只是额度种类换成了对应的那个。
断言一律读 daily_parse_count / daily_chat_count——旧的
daily_summary_count 在工单 #4 之后已无生产写入者，拿它断言等于恒真。

工单 #5 之后一次解析产出三项（总结 / 思维导图 / 标签），事件序列尾部
多了 tags，模型从两次调用变成一次。下面既有用例守的扣减时机没变。
"""
import asyncio
import json

import pytest

import api_summarize
import database
import summarizer
import tags
from seams import StubSummarizer as SeamStubSummarizer

FAR_FUTURE = "2099-01-01T00:00:00+00:00"
SENTINEL = summarizer.MINDMAP_TAGS_SENTINEL


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
    """本文件既有的桩：只数总调用次数，够守「扣几个 / 调没调」。

    新方法的产出形状对齐生产协议（summary token → mindmap → tags）。
    「模型只被调一次」这类逐方法断言用 seams.StubSummarizer，不靠它。
    """

    def __init__(self):
        self.calls = 0

    def summarize_full_stream(self, text, language):
        self.calls += 1
        yield ("summary", "tok-a")
        yield ("summary", "tok-b")
        yield ("mindmap", "# mindmap")
        yield ("tags", ["编程"])

    def chat_stream(self, text, question, history=()):
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


def summarize_req(url="https://example.com/v"):
    return api_summarize.SummarizeRequest(url=url, language="zh")


def chat_req(subtitle="s"):
    return api_summarize.ChatRequest(url="u", question="q", subtitle_text=subtitle)


class ReasonStubExtractor:
    """按给定字典原样返回的提取器——用来喂各种 fail_reason 组合。"""

    def __init__(self, payload):
        self.payload = payload

    def extract(self, url):
        return dict(self.payload)


class TestSubtitleFailureReason:
    """字幕拿不到时，错误事件要说清「为什么」，而不是笼统一句「没有字幕」。

    这里守的是两件事：
    1. 两种结局（「平台没给字幕轨道」与「给了但取不下来」）在用户看得见的
       文案里必须不同——它们对应完全不同的排查方向。
    2. 新键是**可选**的。StubExtractor 至今仍返回旧形状，任何一条没跟上
       新返回结构的路径都不该在这里变成 KeyError。
    """

    def _run(self, monkeypatch, db, make_user, payload, email="u@example.com"):
        monkeypatch.setattr(
            api_summarize, "_get_extractor", lambda: ReasonStubExtractor(payload)
        )
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: StubSummarizer())
        uid = make_user(email)
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))
        return events, uid

    def test_no_track_and_fetch_failure_read_differently(
        self, db, make_user, monkeypatch
    ):
        from summarizer import FAIL_FETCH_FAILED, FAIL_NO_TRACK

        no_track, _ = self._run(monkeypatch, db, make_user, {
            "has_subtitle": False, "full_text": "", "segments": [],
            "fail_reason": FAIL_NO_TRACK,
            "asr_fail_reason": "asr_not_configured",
        }, email="no-track@example.com")
        fetch_failed, _ = self._run(monkeypatch, db, make_user, {
            "has_subtitle": False, "full_text": "", "segments": [],
            "fail_reason": FAIL_FETCH_FAILED,
            "asr_fail_reason": "asr_failed",
        }, email="fetch-failed@example.com")

        no_msg = no_track[1][1]["message"]
        fetch_msg = fetch_failed[1][1]["message"]
        assert no_msg != fetch_msg, "两种落穿不该给出同一句提示"
        assert "没有字幕" in no_msg, no_msg
        assert "没能取下来" in fetch_msg, fetch_msg
        # 运维视角也要能看到「服务端没配语音识别」这条
        assert "未配置语音识别" in no_msg, no_msg
        assert "也没成功" in fetch_msg, fetch_msg

        # 机器可读的原因一并下发，前端将来要分开提示时不必再猜
        assert no_track[1][1]["reason"] == FAIL_NO_TRACK
        assert fetch_failed[1][1]["reason"] == FAIL_FETCH_FAILED
        assert no_track[1][1]["asr_reason"] == "asr_not_configured"

    def test_old_shaped_payload_does_not_raise_key_error(
        self, db, make_user, monkeypatch
    ):
        """提取器仍返回旧形状（无新键）时退回笼统文案，而不是 KeyError。"""
        events, uid = self._run(monkeypatch, db, make_user, {
            "has_subtitle": False, "full_text": "", "segments": [],
        })
        assert [e[0] for e in events] == ["subtitle", "error"], events
        payload = events[1][1]
        assert payload["reason"] == "" and payload["asr_reason"] == ""
        assert "没有可用的字幕" in payload["message"], payload
        assert parse_count_of(uid) == 0, "无字幕却扣了额度"

    def test_chat_stream_reports_reason_too(
        self, db, make_user, monkeypatch
    ):
        from summarizer import FAIL_FETCH_FAILED

        monkeypatch.setattr(api_summarize, "_get_extractor", lambda: ReasonStubExtractor({
            "has_subtitle": False, "full_text": "", "segments": [],
            "fail_reason": FAIL_FETCH_FAILED, "asr_fail_reason": "asr_failed",
        }))
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: StubSummarizer())
        uid = make_user()
        events = collect(
            api_summarize.chat_with_video(chat_req(subtitle=""), user={"id": uid})
        )
        payload = next(e[1] for e in events if e[0] == "error")
        assert payload["reason"] == FAIL_FETCH_FAILED, payload
        assert "无法回答问题" in payload["message"], payload
        assert chat_count_of(uid) == 0, "无字幕却扣了对话额度"


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
            "subtitle", "quota", "summary", "summary", "mindmap", "tags", "done",
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
        """解析额度用满后拒绝，且**不调模型**——这条守的是「拒绝时零成本」。

        每次必须用**不同**的链接：工单 #6 之后同一链接第二次起是免费复用，
        拿同一个 URL 连打并不会耗额度，用例会失去它要测的前提。
        """
        s = stub(has_subtitle=True)
        uid = make_user()
        for i in range(database.DAILY_PARSE_LIMIT):
            collect(
                api_summarize.summarize_video(
                    summarize_req(f"https://example.com/v/{i}"), user={"id": uid}
                )
            )
        events = collect(
            api_summarize.summarize_video(
                summarize_req("https://example.com/v/overflow"), user={"id": uid}
            )
        )
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


class TestSingleCallThreeArtifacts:
    """工单 #5 的验收：一次解析产出总结 + 思维导图 + 标签，模型只被调一次。

    这里用 seams.StubSummarizer（别名 SeamStubSummarizer）而不是本文件那个
    总计数桩：核心 AC 是「模型只被调一次」，而总计数证明不了「调的是哪个
    方法」——接缝建立的起因正是思维导图方法不计数，导致「未调用模型」
    这类断言假通过。
    """

    def _run(self, monkeypatch, make_user, summarizer, email):
        """按给定桩跑一次解析，返回 (事件列表, 用户 id)。"""
        monkeypatch.setattr(
            api_summarize, "_get_extractor", lambda: StubExtractor(has_subtitle=True)
        )
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: summarizer)
        uid = make_user(email)
        return collect(api_summarize.summarize_video(
            summarize_req(), user={"id": uid}
        )), uid

    @staticmethod
    def _payloads(events, name):
        return [payload for kind, payload in events if kind == name]

    def test_three_artifacts_come_out_of_one_request(self, db, make_user, monkeypatch):
        s = SeamStubSummarizer(
            summary_tokens=("甲", "乙"), mindmap="# 主题\n## 章节", tags=("编程", "读书"),
        )
        events, _ = self._run(monkeypatch, make_user, s, "three@example.com")

        assert self._payloads(events, "summary") == ["甲", "乙"], "总结应逐 token 原样下发"
        assert self._payloads(events, "mindmap") == [{"markdown": "# 主题\n## 章节"}]
        assert self._payloads(events, "tags") == [["编程", "读书"]]
        assert [e[0] for e in events][-1] == "done"

    def test_model_is_called_exactly_once(self, db, make_user, monkeypatch):
        """核心 AC：逐方法断言——三个产出同属这一次调用。"""
        s = SeamStubSummarizer()
        self._run(monkeypatch, make_user, s, "once@example.com")

        assert s.calls_of("summarize_full_stream") == 1
        # 「只有这一个方法被调过」比「某个方法没被调」更强：
        # 任何第二处模型调用（哪怕是重名的旧方法）都会让它变红
        assert s.called_methods() == {"summarize_full_stream"}
        assert s.calls_of("generate_mindmap") == 0, "思维导图不该再单独调一次模型"
        assert s.calls_of("summarize_stream") == 0, "总结不该再走那条旧路径"
        assert s.calls == 1, "一次解析只该调一次模型"

    def test_three_artifacts_cost_one_quota(self, db, make_user, monkeypatch):
        """三项产出只扣 1 次：额度条不是每样减 1。"""
        s = SeamStubSummarizer()
        events, uid = self._run(monkeypatch, make_user, s, "quota1@example.com")

        assert parse_count_of(uid) == 1
        assert [k for k, _ in events].count("tags") == 1
        assert [k for k, _ in events].count("mindmap") == 1
        quota = next(p for k, p in events if k == "quota")
        assert quota["remaining"] == database.DAILY_PARSE_LIMIT - 1
        assert db.check_quota_kind(uid, "parse") == (True, database.DAILY_PARSE_LIMIT - 1)

    def test_out_of_vocabulary_tags_never_reach_the_client(self, db, make_user, monkeypatch):
        """模型自创的词一个都不能下发——ADR 0005 说的服务端兜底。"""
        s = SeamStubSummarizer(tags=("AI 编程", "编程", "随便编的"))
        events, _ = self._run(monkeypatch, make_user, s, "vocab@example.com")

        (payload,) = self._payloads(events, "tags")
        assert payload == ["编程"], payload

    def test_empty_tags_fall_back_to_other(self, db, make_user, monkeypatch):
        """模型一个都没给、或给的全是词表外时下发 ["其他"]，数组永不为空。"""
        for tags in ((), ("不在词表里", "也不在")):
            s = SeamStubSummarizer(tags=tags)
            events, _ = self._run(
                monkeypatch, make_user, s, f"empty{len(tags)}@example.com"
            )
            (payload,) = self._payloads(events, "tags")
            assert payload == ["其他"], (tags, payload)

    def test_model_giving_five_tags_is_capped_at_three(self, db, make_user, monkeypatch):
        s = SeamStubSummarizer(tags=("读书", "健身", "旅行", "摄影", "影视"))
        events, _ = self._run(monkeypatch, make_user, s, "cap@example.com")

        (payload,) = self._payloads(events, "tags")
        assert len(payload) == 3, payload
        assert set(payload) <= {"读书", "健身", "旅行", "摄影", "影视"}

    def test_failed_single_call_refunds_the_quota(self, db, make_user, monkeypatch):
        """模型失败不白扣：工单 #4 的唯一 finally 回滚点仍然生效。"""

        class Exploding(SeamStubSummarizer):
            def summarize_full_stream(self, text, language):
                self._calls["summarize_full_stream"] += 1
                yield ("summary", "半个总结")
                raise RuntimeError("模型服务不可用")

        events, uid = self._run(monkeypatch, make_user, Exploding(), "boom@example.com")

        assert [e[0] for e in events][-1] == "error", events
        assert parse_count_of(uid) == 0, "模型失败不该白扣额度"
        assert db.check_quota_kind(uid, "parse") == (True, database.DAILY_PARSE_LIMIT)

    def test_no_subtitle_produces_none_of_the_three(self, db, make_user, monkeypatch):
        """字幕拿不到：三项一个都不产出，模型一次都不调，额度不动。"""
        s = SeamStubSummarizer()
        monkeypatch.setattr(
            api_summarize, "_get_extractor", lambda: StubExtractor(has_subtitle=False)
        )
        monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
        uid = make_user("nosub@example.com")
        events = collect(api_summarize.summarize_video(summarize_req(), user={"id": uid}))

        kinds = [e[0] for e in events]
        assert kinds == ["subtitle", "error"], kinds
        assert not {"summary", "mindmap", "tags"} & set(kinds)
        assert s.calls == 0, "无字幕却调了模型"
        assert parse_count_of(uid) == 0, "无字幕却扣了额度"


class TestDualOutputProtocol:
    """模型侧的分段协议（summarizer.parse_dual_output）。

    协议长这样：

        <总结正文，逐字流式>
        <<<VIDDIGEST_MINDMAP_TAGS>>>
        {"mindmap": "# ...", "tags": ["编程"]}

    协议级测试住在路由测试文件里：工单 #5 的可写范围里没有单独的
    summarizer 测试文件，而这份协议是「一次调用产出三项」的前半段——
    路由拿到的每一项都由它决定。
    """

    @staticmethod
    def _summary_of(events):
        return [p for k, p in events if k == "summary"]

    def test_summary_is_yielded_before_the_stream_ends(self):
        """核心约束：不能先把整个响应读完再吐——那就没有打字机效果了。

        观察点是**上游消费进度**，不是最终事件列表：等流结束再比内容，
        「边收边吐」和「读完再吐」会给出完全相同的结果，断言会恒真。
        """
        consumed = []

        def _upstream():
            for part in ("甲", "乙", SENTINEL, '{"mindmap": "# x", "tags": []}'):
                consumed.append(part)
                yield part

        stream = summarizer.parse_dual_output(_upstream())

        first = next(stream)
        assert first == ("summary", "甲"), first
        assert consumed == ["甲"], (
            f"第一个总结事件之前就把整段流读完了：{consumed}"
        )

    def test_sentinel_cut_across_chunks_is_not_leaked_into_the_summary(self):
        cut = len(SENTINEL) // 2
        events = list(summarizer.parse_dual_output([
            "总结正文", SENTINEL[:cut],
            SENTINEL[cut:] + '{"mindmap": "# x", "tags": ["编程"]}',
        ]))

        assert self._summary_of(events) == ["总结正文"], events
        assert ("mindmap", "# x") in events, events
        assert ("tags", ["编程"]) in events, events

    def test_newline_before_the_sentinel_belongs_to_the_sentinel(self):
        events = list(summarizer.parse_dual_output(
            ["正文\n", SENTINEL, '{"mindmap": "# x", "tags": []}']
        ))

        assert self._summary_of(events) == ["正文"], events

    def test_missing_sentinel_still_streams_the_whole_summary(self):
        """模型不听话时总结不能丢；两条尾事件恒定发出，前端才不用写两套分支。"""
        events = list(summarizer.parse_dual_output(["只有总结", "没有哨兵"]))

        assert self._summary_of(events) == ["只有总结", "没有哨兵"], events
        assert ("mindmap", "") in events, events
        assert ("tags", []) in events, events

    def test_broken_tail_json_degrades_instead_of_raising(self):
        events = list(summarizer.parse_dual_output(["正文", SENTINEL, "{不是 JSON"]))

        assert self._summary_of(events) == ["正文"], events
        assert ("mindmap", "") in events, events
        assert ("tags", []) in events, events

    def test_fenced_json_tail_is_still_parsed(self):
        events = list(summarizer.parse_dual_output([
            "正文", SENTINEL,
            '```json\n{"mindmap": "# x", "tags": ["编程"]}\n```',
        ]))

        assert ("mindmap", "# x") in events, events
        assert ("tags", ["编程"]) in events, events

    def test_wrong_shaped_fields_do_not_leak_types(self):
        """模型输出是不可信输入：类型不对就当没有，不许变成下游 TypeError。"""
        events = list(summarizer.parse_dual_output([
            "正文", SENTINEL, '{"mindmap": {"不是": "字符串"}, "tags": "编程"}',
        ]))

        assert ("mindmap", "") in events, events
        assert ("tags", []) in events, events

    def test_non_object_json_degrades(self):
        events = list(summarizer.parse_dual_output(["正文", SENTINEL, "[1, 2, 3]"]))

        assert ("mindmap", "") in events, events
        assert ("tags", []) in events, events

    def test_prompt_carries_the_vocabulary_and_the_sentinel(self):
        """prompt 少一样东西，模型就少一样产出——而这种失败是沉默的。

        解析器再正确，模型没被告知哨兵行就只会吐出总结、连思维导图和标签
        都没有；词表不进 prompt，模型就只能自创标签。
        """
        prompt = summarizer.VideoSummarizer._build_full_prompt("字幕正文", "zh")

        assert SENTINEL in prompt, "prompt 没告诉模型哨兵行，尾部 JSON 不会被产出"
        assert tags.vocabulary_prompt_text() in prompt, "词表没进 prompt，模型只能自创标签"
        assert "字幕正文" in prompt, "字幕正文没进 prompt"


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

        耗额度时每次必须用**不同**的链接：工单 #6 之后同一链接第二次起是
        免费复用，拿同一个 URL 连打不会耗额度。
        """
        stub(has_subtitle=True)

        # 方向一：对话用满，解析照常
        a = make_user("a@example.com")
        for _ in range(database.DAILY_CHAT_LIMIT):
            collect(api_summarize.chat_with_video(chat_req(), user={"id": a}))
        events = collect(
            api_summarize.summarize_video(
                summarize_req("https://example.com/chat-drained"), user={"id": a}
            )
        )
        assert "error" not in [e[0] for e in events], "对话额度用满不该挡住解析"
        assert parse_count_of(a) == 1

        # 方向二：解析用满，追问照常
        b = make_user("b@example.com")
        for i in range(database.DAILY_PARSE_LIMIT):
            collect(
                api_summarize.summarize_video(
                    summarize_req(f"https://example.com/parse/{i}"), user={"id": b}
                )
            )
        blocked = collect(
            api_summarize.summarize_video(
                summarize_req("https://example.com/parse/overflow"), user={"id": b}
            )
        )
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
