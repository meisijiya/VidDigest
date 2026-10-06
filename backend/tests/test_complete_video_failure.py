"""`complete_video` 返回 0 时必须说人话（工单 #17 第 3 项）。

`complete_video` 的 WHERE 是 `video_url = ? AND status = 'pending'`，
正常流程下返回值必然是 1。返回 0 只在两种情况下发生：

1. 这一行在解析期间被删了——管理员是可以删 pending 行的；
2. 它已经不是 pending 了，成了别人的成果。

原来的处理只有一行 `logger.warning`，然后照样把成功标志置上、
照样结清扣费、照样发 `[DONE]`：用户拿到一份完整总结，社区表里什么都没有，
额度也不退。三件事**各自**看起来都正常，合起来是一次静默的数据丢失。

同一文件里 `regenerate_video` 分支的处理是**对的**——报错、不置成功标记、
退额度。两种处理并存本身就是信号。

本文件造的是**真实的失效**（真的把那一行删掉），不是把返回值改成 0。
所以判据读的是 SSE 事件序列、`users` 表里的计数、`videos` 表里的行，
而不是「有没有调了哪个函数」。
"""
import asyncio
import json

import api_summarize
from seams import StubExtractor
from seams import StubSummarizer as SeamStubSummarizer

URL = "https://example.com/v"


# ── 小工具（与 test_community_videos 同构，刻意不共享）─────────

def collect(gen, on_event=None):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。

    `on_event` 在**每个事件刚发出去、生成器还没往下走**的时候被调用——
    那个位置就是「错误已经告诉用户、但 finally 还没跑」的窗口，
    真实世界里另一个人正好在这个瞬间重新占位。
    """

    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append(
                (e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw))
            )
            if on_event is not None:
                on_event(e)
        return out

    return asyncio.run(_run())


def kinds_of(events):
    return [kind for kind, _ in events]


def messages_of(events):
    return [payload.get("message") for kind, payload in events if kind == "error"]


def parse_count_of(db, uid):
    with db.get_db() as conn:
        return conn.execute(
            "SELECT daily_parse_count FROM users WHERE id = ?", (uid,)
        ).fetchone()[0]


class RowVanishingSummarizer(SeamStubSummarizer):
    """在模型调用的那一刻把占位行删掉——模拟「解析期间被管理员删了」。

    时机是关键：它跑在**扣额度之后、`complete_video` 之前**，
    于是 `complete_video` 真的会因为「行不在了」而返回 0，
    而不是我们把它的返回值改成了 0。
    """

    def __init__(self, db, url, **kw):
        super().__init__(**kw)
        self._db = db
        self._url = url

    def summarize_full_stream(self, text, language):
        with self._db.get_db() as conn:
            conn.execute("DELETE FROM videos WHERE video_url = ?", (self._url,))
        yield from super().summarize_full_stream(text, language)


def wire(monkeypatch, summarizer=None):
    summarizer = summarizer if summarizer is not None else SeamStubSummarizer()
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: summarizer)
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: StubExtractor())
    return summarizer


def summarize(uid):
    return api_summarize.summarize_video(
        api_summarize.SummarizeRequest(url=URL, language="zh"),
        user={"id": uid},
    )


# ── 阳性对照：没有失效时，一切照旧 ────────────────────────────

class TestSuccessPathUnchanged:
    """没有这条，下面那些失败用例就没有对照——「不发 error」既可能是
    正确处理的结果，也可能是整条路根本没跑起来。"""

    def test_normal_parse_still_reports_done_and_keeps_the_quota(
        self, db, make_user, monkeypatch
    ):
        wire(monkeypatch)
        uid = make_user("a@example.com")

        events = collect(summarize(uid))

        assert kinds_of(events)[-1] == "done", kinds_of(events)
        assert "error" not in kinds_of(events), kinds_of(events)
        row = db.get_video_by_url(URL)
        assert row["status"] == "ready", "前提：正常解析该落库"
        assert parse_count_of(db, uid) == 1, "成功的那一次不该退额度"


# ── 落库失败：三件事都不能装作正常 ────────────────────────────

class TestCompleteVideoRefusalIsLoud:
    def test_it_reports_an_error_instead_of_done(self, db, make_user, monkeypatch):
        wire(monkeypatch, RowVanishingSummarizer(db, URL, tags=("编程",)))
        uid = make_user("a@example.com")

        events = collect(summarize(uid))

        kinds = kinds_of(events)
        assert "done" not in kinds, (
            f"落库失败了却报了完成：{kinds}"
        )
        assert "error" in kinds, f"落库失败没有任何提示：{kinds}"
        assert messages_of(events), "error 事件没有可读的 message"

    def test_the_quota_is_refunded(self, db, make_user, monkeypatch):
        """用户看到了一份完整总结，社区里却什么都没有。

        让他白付这一次不成立——而且白扣的额度没有任何痕迹可查。
        """
        wire(monkeypatch, RowVanishingSummarizer(db, URL, tags=("编程",)))
        uid = make_user("a@example.com")

        collect(summarize(uid))

        assert parse_count_of(db, uid) == 0, (
            "落库失败却没有退额度：用户拿到了总结，社区里没有，额度也没了"
        )

    def test_no_phantom_entry_appears_in_the_community(self, db, make_user, monkeypatch):
        wire(monkeypatch, RowVanishingSummarizer(db, URL, tags=("编程",)))
        uid = make_user("a@example.com")

        collect(summarize(uid))

        assert db.list_community_videos()["items"] == [], (
            "解析失败却在社区里留下一条内容"
        )


# ── 失败路径不许顺手删掉别人的位置 ────────────────────────────

class TestFailureDoesNotTouchAPlaceholderWeNeverOwned:
    def test_a_placeholder_taken_after_the_error_survives(
        self, db, make_user, monkeypatch
    ):
        """错误事件抛出去之后、本次请求的 finally 跑之前，有人重新占位了。

        真实世界里这是很窄的一个窗口，但后果是**别人的整次解析被删掉**：
        他的占位行没了，finally 不会发 done，社区里也永远不会有他那份总结。

        判据盯的是那一行还在不在、还归谁——不是「有没有调过 release」。
        """
        wire(monkeypatch, RowVanishingSummarizer(db, URL, tags=("编程",)))
        first = make_user("a@example.com")
        taken = {}

        def on_event(event):
            if event.event == "error" and "second" not in taken:
                taken["second"] = make_user("b@example.com")
                outcome, _ = db.reserve_video(URL, taken["second"])
                assert outcome == "reserved", f"前提不成立：B 被判成 {outcome}"

        collect(summarize(first), on_event=on_event)

        row = db.get_video_by_url(URL)
        assert row is not None, (
            "后来人重新占下的位置，被这次失败的请求还回去了"
        )
        assert row["status"] == "pending"
        assert row["parsed_by"] == taken["second"], "占位的主人被换掉了"