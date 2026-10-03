"""多用户隔离：A 的东西 B 看不到，B 的东西 A 看不到。

社区视频表是**全局**的——这是产品决定（ADR 0001：「共用内容是公共品」），
不是泄漏。所以这个文件先把「哪些是共享的」划清楚，再守真正的隔离边界。
不划清就会出现两种坏测试：一种把共享内容当泄漏来测（测错了对象），
另一种把「读不到」当隔离来测（工单 #8 的教训：那条一度成立的原因是
**谁都读不到**，不是隔离生效）。

四类边界，逐类都有「把整条能力删掉它还会绿吗」的对照：

1. **写权限按人**（ADR 0007）：覆盖只能由 `parsed_by` 本人做。删掉这条
   判定后，全站任何人都能改写社区内容——而所有复用测试仍然全绿。
2. **追问会话按人**：A 问的问题 B 读不到，且**两边都真的读得到自己那份**
   （后者防的是「读出口整个断掉」这种假隔离）。
3. **解析历史按人**：B 的列表里没有 A 的记录；A 删自己的不影响 B。
4. **额度按人**：A 解析不扣 B 的额度。

刻意用**真线程 + 强制交错**测并发：两个人同时对同一条发起覆盖时，
单线程顺序跑测不出任何东西——而这正是「谁的覆盖生效」出问题的地方。
"""
import asyncio
import json
import sqlite3
import threading

import pytest

import api_summarize
import auth
import database
import summarizer
from seams import StubExtractor, auth_headers, make_client
from seams import StubSummarizer as SeamStubSummarizer

URL = "https://www.bilibili.com/video/BV1multiUser"
OTHER_URL = "https://www.bilibili.com/video/BV1otherUser"


# ── 小工具 ─────────────────────────────────────────────────

def collect(gen):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。"""
    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
        return out

    return asyncio.run(_run())


def summarize(url=URL, uid=None, overwrite=False):
    return api_summarize.summarize_video(
        api_summarize.SummarizeRequest(url=url, language="zh", overwrite=overwrite),
        user=None if uid is None else {"id": uid},
    )


def wire(monkeypatch, summarizer_stub=None, extractor=None):
    s = summarizer_stub if summarizer_stub is not None else SeamStubSummarizer()
    e = extractor if extractor is not None else StubExtractor()
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: e)
    return s, e


def kinds_of(events):
    return [k for k, _ in events]


def payload_of(events, name):
    return [p for k, p in events if k == name]


def parse_count(db, uid):
    with db.get_db() as c:
        return c.execute(
            "SELECT daily_parse_count FROM users WHERE id = ?", (uid,)
        ).fetchone()[0]


def seed_owned(db, owner, url=URL, summary="A 写的总结"):
    """造一条 ready 且属于 owner 的社区内容。"""
    db.reserve_video(url, owner)
    db.complete_video(url, summary_md=summary, mindmap_md="# A", tags=["编程"],
                      subtitle_text="字幕")
    return db.get_video_by_url(url)


@pytest.fixture()
def trio(db, make_user):
    """三个账号：A（作者）、B（陌生人）、C（另一个陌生人）。"""
    return (
        make_user("alice@example.com"),
        make_user("bob@example.com"),
        make_user("carol@example.com"),
    )


# ── 0. 先把「共享」与「隔离」划清楚 ──────────────────────────

class TestWhatIsSharedOnPurpose:
    def test_b_sees_as_content_and_pays_nothing(self, db, trio, monkeypatch):
        """共享是产品决定，不是泄漏。

        这条存在的意义是给下面的隔离测试**划界**：如果没有它，
        有人会顺手写一条「B 看不到 A 的总结」并把它当隔离通过。
        """
        a, b, _c = trio
        s, _ = wire(monkeypatch, SeamStubSummarizer(
            summary_tokens=("甲", "乙"), mindmap="# 主题", tags=("编程",)))
        collect(summarize(uid=a))
        before = parse_count(db, b)

        events = collect(summarize(uid=b))

        assert payload_of(events, "summary") == ["甲乙"], events
        assert parse_count(db, b) == before, "复用不该扣 B 的额度"
        assert s.calls_of("summarize_full_stream") == 1, "B 又调了一次模型"

    def test_the_community_table_has_exactly_one_row_for_the_url(self, db, trio):
        """「全站只有一份」是物理事实，不只是接口约定。"""
        a, b, _c = trio
        seed_owned(db, a)
        with db.get_db() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM videos WHERE video_url = ?", (URL,)
            ).fetchone()[0]
        assert n == 1, "同一个链接出现了多行，B 看到的和 A 写的可能不是同一份"


# ── 1. 写权限按人：重新解析 ──────────────────────────────────

class TestOnlyTheOwnerMayRegenerate:
    def test_a_may_overwrite_and_b_may_not(self, db, trio, monkeypatch):
        """A 改自己的行成功；B 对同一行发起覆盖被拒。

        两个判定写在一条里：B 那次**不能**改变任何可观察的东西
        （内容、标签、额度），否则「被拒」只是个提示。
        """
        a, b, _c = trio
        seed_owned(db, a, summary="A 的原话")
        s, _ = wire(monkeypatch, SeamStubSummarizer(
            summary_tokens=("A", "改", "写"), tags=("读书",)))

        denied = collect(summarize(uid=b, overwrite=True))
        assert kinds_of(denied) == ["error"], denied
        assert db.get_video_by_url(URL)["summary_md"] == "A 的原话", "B 改写了 A 的内容"
        assert db.get_video_by_url(URL)["tags"] == ["编程"]
        assert parse_count(db, b) == 0, "被拒却扣了 B 的额度"
        assert s.calls_of("summarize_full_stream") == 0, "被拒之前就调了模型"

        allowed = collect(summarize(uid=a, overwrite=True))
        assert kinds_of(allowed)[-1] == "done", allowed
        # 桩把 token 逐个 yield 出来、路由无分隔符拼接，所以这里是「A改写」
        assert db.get_video_by_url(URL)["summary_md"] == "A改写"
        assert parse_count(db, a) == 1

    def test_c_is_refused_just_like_b(self, db, trio, monkeypatch):
        """第三个账号也要挡住。

        只测 B 的话，一个「拒绝名单是 [B]」的实现照样全绿。
        """
        a, _b, c = trio
        seed_owned(db, a, summary="A 的原话")
        wire(monkeypatch)

        events = collect(summarize(uid=c, overwrite=True))

        assert kinds_of(events) == ["error"], events
        assert db.get_video_by_url(URL)["summary_md"] == "A 的原话"

    def test_the_ownership_does_not_transfer_on_overwrite(self, db, trio, monkeypatch):
        """A 覆盖之后，所有权仍是 A。

        判据是覆盖**之后** B 仍被拒：把 parsed_by 改成覆盖者这种实现
        （哪怕是同一个值）在别的场景下会出事，而这里最省事的写法恰好
        是「UPDATE 时顺手把 parsed_by 设成当前用户」。
        """
        a, b, _c = trio
        seed_owned(db, a)
        wire(monkeypatch, SeamStubSummarizer(summary_tokens=("新的",)))
        collect(summarize(uid=a, overwrite=True))

        assert db.get_video_by_url(URL)["parsed_by"] == a
        after = collect(summarize(uid=b, overwrite=True))
        assert kinds_of(after) == ["error"], after

    def test_a_null_owned_row_belongs_to_nobody(self, db, trio, monkeypatch):
        """parsed_by 为 NULL 的行：三个人都改不了，但内容照样公开可读。"""
        a, b, c = trio
        now = "2026-01-01T00:00:00+00:00"
        with db.get_db() as conn:
            conn.execute(
                """INSERT INTO videos (video_url, status, parsed_by, created_at, updated_at)
                   VALUES (?, 'ready', NULL, ?, ?)""", (URL, now, now))
        wire(monkeypatch)

        for uid in (a, b, c):
            events = collect(summarize(uid=uid, overwrite=True))
            assert kinds_of(events) == ["error"], f"用户 {uid} 改写了无主的内容"
        assert db.get_video_by_url(URL)["summary_md"] == ""

    def test_the_direct_sql_gate_holds_without_going_through_the_route(
        self, db, trio
    ):
        """判定在 SQL 里，不在路由的 if 里。

        绕开路由直接调数据层：这是「结构性约束」的测法——
        路由层的 if 可以在某条新路径上被绕过，数据层的 WHERE 不会。
        """
        a, b, _c = trio
        seed_owned(db, a, summary="A 的原话")

        assert database.regenerate_video(URL, a, summary_md="A 改的") == 1
        assert db.get_video_by_url(URL)["summary_md"] == "A 改的"
        assert database.regenerate_video(URL, b, summary_md="B 改的") == 0
        assert db.get_video_by_url(URL)["summary_md"] == "A 改的", "B 绕过了路由"


# ── 2. 追问会话按人 ─────────────────────────────────────────

class TestChatSessionsArePerUser:
    @staticmethod
    def _ask(url, uid, question, answer):
        api_summarize.append_chat_turn(uid, url, question, answer)

    def test_a_cannot_read_bs_transcript(self, db, trio):
        a, b, _c = trio
        seed_owned(db, a)
        self._ask(URL, a, "A 问的私密问题", "A 的答案")
        self._ask(URL, b, "B 问的私密问题", "B 的答案")

        a_sees = database.get_recent_chat_messages(a, URL, 10)
        b_sees = database.get_recent_chat_messages(b, URL, 10)

        # 一轮追问落两行（问 + 答），读取时按 id 升序原样返回
        assert [m["content"] for m in a_sees] == ["A 问的私密问题", "A 的答案"], a_sees
        assert [m["content"] for m in b_sees] == ["B 问的私密问题", "B 的答案"], b_sees
        assert "B 的答案" not in json.dumps(a_sees, ensure_ascii=False)
        assert "A 的答案" not in json.dumps(b_sees, ensure_ascii=False)

    def test_a_user_with_no_questions_reads_empty_not_others(self, db, trio):
        """C 没问过 → 读到空列表，且不是「读不到」这种假隔离。

        工单 #8 的教训：那条测试一度成立的原因是**谁的记录都读不到**。
        所以这里必须同时验「C 读到空」与「A 读到自己那份」。
        """
        a, _b, c = trio
        seed_owned(db, a)
        self._ask(URL, a, "A 的问题", "A 的答案")

        assert database.get_recent_chat_messages(c, URL, 10) == []
        assert len(database.get_recent_chat_messages(a, URL, 10)) == 2, (
            "隔离生效了？还是这个读出口整个断了？"
        )

    def test_the_http_session_endpoint_is_scoped_to_the_caller(
        self, db, trio
    ):
        """走真实 HTTP：两个 token 读同一个视频，各自只拿到自己那份。"""
        import main as main_module

        a, b, _c = trio
        seed_owned(db, a)
        self._ask(URL, a, "A 的问题", "A 的答案")
        self._ask(URL, b, "B 的问题", "B 的答案")

        with make_client(main_module.app) as client:
            ra = client.get("/api/history/chat", params={"url": URL},
                            headers=auth_headers(auth.create_token(a, "alice@example.com")))
            rb = client.get("/api/history/chat", params={"url": URL},
                            headers=auth_headers(auth.create_token(b, "bob@example.com")))

        assert "A 的答案" in ra.text
        assert "B 的答案" not in ra.text, "A 读到了 B 的追问记录"
        assert "B 的答案" in rb.text
        assert "A 的答案" not in rb.text, "B 读到了 A 的追问记录"

    def test_chat_requires_login(self, db, trio):
        import main as main_module

        _a, _b, _c = trio
        seed_owned(db, _a)
        with make_client(main_module.app) as client:
            r = client.get("/api/history/chat", params={"url": URL})
        assert r.status_code == 401


# ── 3. 解析历史按人 ─────────────────────────────────────────

class TestParseHistoryIsPerUser:
    def test_b_list_does_not_contain_as_record(self, db, trio):
        a, b, _c = trio
        seed_owned(db, a)
        database.upsert_parse_history(a, URL, "A 的标题", summary_md="A 的总结")

        b_items = database.get_parse_histories(b)
        assert b_items == [], f"B 的历史里出现了 A 的记录：{b_items}"
        a_items = database.get_parse_histories(a)
        assert len(a_items) == 1, "A 读不到自己的记录（隔离过头了？）"

    def test_b_cannot_read_as_record_detail_by_id(self, db, trio):
        """按 id 取详情：猜到 id 也不行。"""
        a, b, _c = trio
        seed_owned(db, a)
        hid = database.upsert_parse_history(a, URL, "A 的标题", summary_md="A 的总结")

        assert database.get_parse_history_detail(b, hid) is None, (
            "B 拿着 A 的 history_id 读到了 A 的记录"
        )
        assert database.get_parse_history_detail(a, hid) is not None

    def test_deleting_a_record_leaves_b_alone(self, db, trio):
        a, b, _c = trio
        seed_owned(db, a)
        ha = database.upsert_parse_history(a, URL, "A", summary_md="x")
        hb = database.upsert_parse_history(b, URL, "B", summary_md="y")

        database.delete_parse_history(a, ha)

        assert database.get_parse_history_detail(b, hb) is not None, (
            "A 删自己的记录，把 B 的也删了"
        )
        assert database.get_parse_history_detail(a, ha) is None

    def test_b_cannot_delete_as_record_even_with_its_id(self, db, trio):
        """B 拿着 A 的 history_id 调删除：必须失败，且 A 的记录还在。

        `test_deleting_a_record_leaves_b_alone` 守不住这个——那一对 id 本来就不同，
        按主键删天然不会碰到 B 的行。真正承重的是**调用者**这一列，
        拆掉 delete_parse_history 里的 `user_id = ?` 之后，本条会转红。
        """
        a, b, _c = trio
        seed_owned(db, a)
        ha = database.upsert_parse_history(a, URL, "A 的标题", summary_md="A 的总结")

        assert database.delete_parse_history(b, ha) is False, "B 删掉了 A 的记录"
        assert database.get_parse_history_detail(a, ha) is not None, (
            "B 拿着 A 的 history_id 把记录删了"
        )

    def test_the_legacy_chat_column_is_scoped_too(self, db, trio):
        """老列回退路径也必须按人过滤。

        `chat_messages` 新表为空时 `get_chat_session` 会回退去读
        `parse_history.chat_history` 老列。上面那些用例全走新表，老列一直是空的，
        整条分支没被执行过一次——漏掉 user_id 过滤就是**别人的追问记录被读出来**。
        """
        a, b, _c = trio
        seed_owned(db, a)
        legacy = json.dumps(
            [{"question": "A 的老问题", "answer": "A 的老答案"}], ensure_ascii=False)
        database.upsert_parse_history(a, URL, "A", summary_md="x")
        with db.get_db() as conn:
            conn.execute(
                "UPDATE parse_history SET chat_history = ? WHERE user_id = ?",
                (legacy, a),
            )

        assert database.get_chat_session(a, URL) == [
            {"question": "A 的老问题", "answer": "A 的老答案"}], "A 读不到自己的老记录"
        assert database.get_chat_session(b, URL) == [], "B 读到了 A 的老追问记录"
        assert database.get_chat_session(_c, URL) == []

    def test_the_legacy_fallback_is_reachable_through_the_detail_endpoint(
        self, db, trio
    ):
        """上面那条不能只在数据层自证：走真实 HTTP 再验一遍。"""
        import main as main_module

        a, b, _c = trio
        seed_owned(db, a)
        legacy = json.dumps(
            [{"question": "A 的老问题", "answer": "A 的老答案"}], ensure_ascii=False)
        hid = database.upsert_parse_history(a, URL, "A", summary_md="x")
        with db.get_db() as conn:
            conn.execute("UPDATE parse_history SET chat_history = ? WHERE id = ?",
                         (legacy, hid))

        with make_client(main_module.app) as client:
            ra = client.get(f"/api/history/{hid}",
                            headers=auth_headers(auth.create_token(a, "alice@example.com")))
            rb = client.get(f"/api/history/{hid}",
                            headers=auth_headers(auth.create_token(b, "bob@example.com")))

        assert ra.status_code == 200, ra.text
        assert "A 的老答案" in ra.text
        assert rb.status_code == 404, f"B 拿到了别人的记录：{rb.status_code}"


# ── 4. 额度按人 ─────────────────────────────────────────────

class TestQuotaIsPerUser:
    def test_as_parse_does_not_spend_b(self, db, trio, monkeypatch):
        a, b, _c = trio
        wire(monkeypatch)
        before_a, before_b = parse_count(db, a), parse_count(db, b)

        collect(summarize(uid=a))

        assert parse_count(db, a) == before_a + 1
        assert parse_count(db, b) == before_b, "A 解析扣了 B 的额度"

    def test_a_rejected_overwrite_leaves_b_untouched(self, db, trio, monkeypatch):
        """被拒的请求不该在**任何**账号上留下痕迹。"""
        a, b, _c = trio
        seed_owned(db, a)
        wire(monkeypatch)
        before_b = parse_count(db, b)

        collect(summarize(uid=b, overwrite=True))

        assert parse_count(db, b) == before_b

    def test_a_failed_overwrite_refunds_only_its_own(self, db, trio, monkeypatch):
        """A 的覆盖失败要退款，但不能顺手把 B 的计数也动了。"""
        a, b, _c = trio

        class Boom(SeamStubSummarizer):
            def summarize_full_stream(self, text, language):
                raise RuntimeError("模型挂了")

        wire(monkeypatch, Boom())
        seed_owned(db, a)
        before_a, before_b = parse_count(db, a), parse_count(db, b)

        events = collect(summarize(uid=a, overwrite=True))

        assert kinds_of(events)[-1] == "error", events
        assert parse_count(db, a) == before_a, "失败的覆盖没有退款"
        assert parse_count(db, b) == before_b


# ── 5. 并发：两个人同时对同一条发起覆盖 ─────────────────────

class TestConcurrentRegenerate:
    def test_only_the_owner_wins_even_when_both_arrive_together(
        self, db, trio, monkeypatch
    ):
        """A 与 B 同时发起覆盖：只有 A 生效，B 被拒，内容是 A 的。

        同步点放在**模型桩**里：覆盖在落库前必然经过它，而正确实现下
        B 根本不会进模型，所以这里不会互相等待。
        """
        a, b, _c = trio
        seed_owned(db, a, summary="A 的原话")
        entered, release = threading.Event(), threading.Event()

        class _Gated(SeamStubSummarizer):
            def summarize_full_stream(self, text, language):
                entered.set()
                assert release.wait(timeout=10), "门没被打开，测试会挂死"
                yield from super().summarize_full_stream(text, language)

        wire(monkeypatch, _Gated(summary_tokens=("A", "改", "写")))
        out = {}

        t = threading.Thread(target=lambda: out.update(a=collect(summarize(uid=a, overwrite=True))))
        t.start()
        assert entered.wait(timeout=10), "A 的覆盖根本没开始"

        out["b"] = collect(summarize(uid=b, overwrite=True))
        release.set()
        t.join(timeout=15)
        assert not t.is_alive()

        assert kinds_of(out["a"])[-1] == "done", out["a"]
        assert kinds_of(out["b"]) == ["error"], out["b"]
        row = db.get_video_by_url(URL)
        assert row["summary_md"] == "A改写", row["summary_md"]
        assert row["parsed_by"] == a

    def test_two_owners_on_different_accounts_cannot_both_win(
        self, db, trio, monkeypatch
    ):
        """反面对照：所有权只有一个，A 成功了就说明 B 必然失败。

        没有第一条的话，一个「两个人都成功」的实现也能让它变绿。
        """
        a, b, _c = trio
        seed_owned(db, a, summary="A 的原话")
        s, _ = wire(monkeypatch, SeamStubSummarizer(summary_tokens=("新的",)))

        ok_a = collect(summarize(uid=a, overwrite=True))
        ok_b = collect(summarize(uid=b, overwrite=True))

        assert kinds_of(ok_a)[-1] == "done"
        assert kinds_of(ok_b) == ["error"]
        assert s.calls_of("summarize_full_stream") == 1, (
            f"模型被调了 {s.calls_of('summarize_full_stream')} 次，B 那次本不该发生"
        )
