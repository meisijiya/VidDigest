"""还位只删「本来就是我占的那一行」（工单 #17 第 5 项）。

票面写的是「读代码推断，未实测」。这里把它变成实测——而且**不需要**
任何 TTL 注入点：`VIDEO_PENDING_TTL_SECONDS` 虽是 1800 秒，
但 `_pending_is_stale` 读的是模块里的那个常量，既有测试一直用
「把 updated_at 往前推」的手法把占位推老，那是同一条接缝。

失效链（本文件守的整条）：

1. A 占位（``parsed_by = A``），模型/连接挂住超过 TTL；
2. B 请求同一链接 → ``_pending_is_stale`` 为真 → B 接管，``parsed_by = B``；
3. A 随后失败或断流 → ``finally`` 调 ``release_video``；
4. 原来的 WHERE 只看 ``video_url + status='pending'``，
   于是**A 把 B 的占位删掉了**；
5. B 的 ``complete_video`` 返回 0。

第 5 步在工单 #17 第 3 项之后已经不是静默丢数据了（B 会拿到 error 事件
并退款），但 B 的整次解析仍然被 A 毁掉——他从头到尾什么都没做错。

判据读的是 videos 表里实际的行与主人，不测私有函数。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import api_summarize
from seams import StubExtractor
from seams import StubSummarizer as SeamStubSummarizer

URL = "https://example.com/v"


# ── 小工具 ─────────────────────────────────────────────────

def collect(gen):
    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append(
                (e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw))
            )
        return out

    return asyncio.run(_run())


def age_row(db, seconds: int) -> None:
    """把占位行的 updated_at 往前推，模拟「占位者已经死了这么久」。"""
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db.get_db() as conn:
        conn.execute("UPDATE videos SET updated_at = ?", (old,))


def row_of(db, url=URL):
    return db.get_video_by_url(url)


class HandoverThenFailSummarizer(SeamStubSummarizer):
    """在模型调用的那一刻先让 B 接管 A 的陈旧占位，然后让 A 失败。

    时机是关键：接管发生在**扣额度之后、A 的 finally 之前**，
    于是整条失效链是真的跑出来的，而不是我们把 delete 改成报错。
    """

    def __init__(self, takeover, **kw):
        super().__init__(**kw)
        self._takeover = takeover

    def summarize_full_stream(self, text, language):
        self._takeover()
        raise RuntimeError("模型挂死了")
        yield  # pragma: no cover —— 只为让它是个生成器


def summarize(uid):
    return api_summarize.summarize_video(
        api_summarize.SummarizeRequest(url=URL, language="zh"),
        user={"id": uid},
    )


def wire(monkeypatch, summarizer):
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: summarizer)
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: StubExtractor())


# ── 数据层：还位只删自己占的那一行 ──────────────────────────

class TestReleaseOnlyDeletesWhatYouOwn:
    def test_the_owner_can_still_release_its_own_placeholder(self, db, make_user):
        """阳性对照：没有这条，「永不删任何东西」也会让下面全绿。"""
        a = make_user("a@example.com")
        assert db.reserve_video(URL, a)[0] == "reserved"

        assert db.release_video(URL, a) == 1, "占位者自己却还不了位"
        assert row_of(db) is None

    def test_a_placeholder_taken_over_by_someone_else_survives(self, db, make_user):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        db.reserve_video(URL, a)
        age_row(db, db.VIDEO_PENDING_TTL_SECONDS + 60)
        assert db.reserve_video(URL, b)[0] == "reserved", "前提：B 没能接管"

        assert db.release_video(URL, a) == 0, (
            "A 删掉了 B 的占位：B 的整次解析被 A 的失败动作毁掉了"
        )

        row = row_of(db)
        assert row is not None, "B 的占位整行没了"
        assert row["status"] == "pending"
        assert row["parsed_by"] == b, "占位的主人被换掉了"

    def test_an_anonymous_placeholder_is_releasable_only_by_null(self, db):
        """parsed_by 可为 NULL，`=` 判不出「相等」，必须用 IS。

        建表早期的记录与匿名占位都是 NULL，而调用方传的也是 None。
        写成 `=` 的话这一行永远删不掉——链接就此永久卡在 pending。
        """
        assert db.reserve_video(URL, None)[0] == "reserved"

        assert db.release_video(URL, None) == 1, "匿名占位永远还不了位"
        assert row_of(db) is None


class FailingSummarizer(SeamStubSummarizer):
    """什么都不做就失败——用来验「自己的占位仍然会被还回去」。"""

    def summarize_full_stream(self, text, language):
        raise RuntimeError("模型挂了")
        yield  # pragma: no cover —— 只为让它是个生成器


# ── 端到端：A 失败时不该毁掉 B 的解析 ───────────────────────

class TestAFailedRequestDoesNotDestroyBTakesParse:
    def test_b_keeps_his_placeholder_when_a_fails_after_the_takeover(
        self, db, make_user, monkeypatch
    ):
        a = make_user("a@example.com")
        b = make_user("b@example.com")
        taken = {}

        def takeover():
            # 把 A 的占位推老，让 B 真的抢到位置。
            age_row(db, db.VIDEO_PENDING_TTL_SECONDS + 60)
            outcome, _row = db.reserve_video(URL, b)
            assert outcome == "reserved", f"前提不成立：B 被判成 {outcome}"
            taken["b"] = b

        wire(monkeypatch, HandoverThenFailSummarizer(takeover))

        events = collect(summarize(a))

        assert taken, "前提不成立：接管根本没发生"
        assert [k for k, _ in events][-1] == "error", events
        row = row_of(db)
        assert row is not None, (
            "A 的失败动作把 B 正在进行的解析删掉了：B 什么都不知道，"
            "他的 finally 不会再发 done，社区里也永远不会有他那份总结"
        )
        assert row["parsed_by"] == b
        assert row["status"] == "pending"

    def test_a_failed_request_still_releases_its_own_placeholder(
        self, db, make_user, monkeypatch
    ):
        """反向：加了守卫之后，**自己**的占位照样要还回去。

        留着这一条是因为「永不删任何东西」是修这个 bug 最省事的写法，
        而且它让上面所有用例都绿——症状是那个链接从此永久卡在 pending，
        每次解析都要干等 30 秒再失败。留着行 pending 的代价与本项要修的
        那个 bug 一样大。
        """
        a = make_user("a@example.com")
        wire(monkeypatch, FailingSummarizer())

        collect(summarize(a))

        assert row_of(db) is None, (
            "失败的请求没有还回自己的占位：这个链接从此永久卡在 pending，"
            "每个后来者都要干等到超时"
        )
