"""工单 #9 第二部分：自带凭据的追问（流式同形 / 不扣额度 / 不落盘 / 不回显）。

断言只落在外部可观察的地方：HTTP 响应里的 SSE 事件、送到模型那里的入参、
数据库最终状态、以及**整条请求跑完之后的日志与标准流**。
私有函数不测，也不拿桩的调用次数冒充「模型被调了没有」——
这里连桩都不用：`summarizer.OpenAI` 被换成一个如实收下 api_key 与 messages
的假客户端，于是**真的** ``VideoSummarizer.chat_stream`` 在跑，
它构造出来的 messages 就是模型将收到的那一份。

「未落盘」的证明方式：逐表逐列**查询**（见 ``every_stored_value``），
不是去扫 .db 文件的字节。票面点名过这条——WAL 模式下已删除的数据字节仍留在
文件里，扫文件证明不了任何事，它只会给一个「碰巧没有」的假通过。

这批测试能抓住的：额度被扣、「统一在入口做额度检查」把额度豁免盖掉、
异常文本被原样回显、凭据进了日志/标准流/某张表、流式事件序列与平台路径分叉、
用户填的 key 没能真正到达模型客户端。
抓不住的：真实服务商的鉴权行为（假客户端不校验 key）、
以及将来有人绕过 UserCredential 直接用裸字符串——那是复审数 reveal() 调用点的活。
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import api_summarize
import auth
import database
import summarizer
from seams import auth_headers, make_client

URL = "https://example.com/v/byok"

#: 只在这个文件里出现的哨兵，任何服务商都不会签发这种形态。
SENTINEL = "sk-agent-bytok-9f3c1a7e-ZZUNIQUEZZ"

PLATFORM_KEY = "platform-key-for-test-only"

#: 本项目自己的日志器名字（取点号前的第一段）。
#: 用它把「本项目记了日志」与「测试装置记了日志」分开判。
APP_LOGGERS = {"api_summarize", "summarizer", "credentials",
               "auth", "database"}


# ── 假模型客户端 ────────────────────────────────────────────

class ProviderAuthError(RuntimeError):
    """模拟「第三方 SDK 的鉴权异常可能带请求体片段」。

    这是本工单最大的坑：真实异常里可能回显 key 的前若干位。
    所以它同时把凭据放进**异常消息**和**异常属性**——
    任何一处被 str()、repr()、日志格式化碰到都会泄漏。
    """

    def __init__(self, key):
        super().__init__(f"401 Error: incorrect API key provided: {key}")
        self.request_headers = {"Authorization": f"Bearer {key}"}


class FakeOpenAI:
    """替换 openai 客户端：不联网、不花钱，但如实记下 api_key 与入参。"""

    calls = []      # [(api_key, base_url, kwargs), ...]
    raises = None   # 非空则让下一次 create 抛它
    tokens = ("答案片段",)

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        FakeOpenAI.calls.append(
            (self.kwargs.get("api_key"), self.kwargs.get("base_url"), kwargs)
        )
        if FakeOpenAI.raises is not None:
            raise FakeOpenAI.raises
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])
            for t in FakeOpenAI.tokens
        ]


@pytest.fixture()
def fake_openai(monkeypatch):
    """把 openai 客户端换成假客户端，并把平台 key 指向一个假值。

    平台 key 必须也换成假值：否则不带凭据的那条路径会拿 backend/.env 里的
    真 key 建客户端——即使不发网络请求，那也是把真凭据带进测试进程。
    """
    FakeOpenAI.calls = []
    FakeOpenAI.raises = None
    FakeOpenAI.tokens = ("答案片段",)
    monkeypatch.setattr(summarizer, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", PLATFORM_KEY)
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: summarizer.VideoSummarizer())
    return FakeOpenAI


@pytest.fixture()
def app(db):
    """真实 app（含真实鉴权依赖），只把库指向临时库。"""
    import main as main_module
    return main_module.app


# ── 工具 ──────────────────────────────────────────────────

def post_chat(app, uid, question="问题", api_key=None, email="a@example.com"):
    payload = {"url": URL, "question": question}
    if api_key is not None:
        payload["user_api_key"] = api_key
    with make_client(app) as c:
        return c.post("/api/chat", json=payload,
                      headers=auth_headers(auth.create_token(uid, email)))


def event_list(response_text):
    """SSE 响应里的事件名序列。"""
    return [line[7:] for line in response_text.splitlines() if line.startswith("event: ")]


def event_pairs(response_text):
    """SSE 响应里的 [(事件名, 负载)]。"""
    lines = response_text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if not line.startswith("event: "):
            continue
        data = None
        if i + 1 < len(lines) and lines[i + 1].startswith("data: "):
            raw = lines[i + 1][6:]
            try:
                data = json.loads(raw)
            except ValueError:
                data = raw
        out.append((line[7:], data))
    return out


def quota_event(response_text):
    for name, data in event_pairs(response_text):
        if name == "quota":
            return data
    raise AssertionError(f"响应里没有 quota 事件：{event_list(response_text)}")


def messages_of(call):
    return call[2]["messages"]


def chat_count_of(uid):
    with database.get_db() as c:
        row = c.execute(
            "SELECT daily_chat_count FROM users WHERE id=?", (uid,)
        ).fetchone()
    return row["daily_chat_count"]


def exhaust_chat_quota(uid):
    """把对话额度真的用光（计数与日期都要对，只改计数会被日期判成「今天还没用过」）。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with database.get_db() as c:
        c.execute(
            "UPDATE users SET daily_chat_count=?, last_chat_date=? WHERE id=?",
            (database.DAILY_CHAT_LIMIT, today, uid),
        )


def seed_community_video(url=URL, subtitle="社区视频表里的字幕原文"):
    outcome, _row = database.reserve_video(url, None)
    assert outcome == "reserved", f"前提不成立：{url} 被 reserve 判成 {outcome}"
    assert database.complete_video(url, subtitle_text=subtitle) == 1
    return url


def every_stored_value():
    """逐表逐列地把**存下来的值**取出来。

    这是「查询每一张业务表的每一列」，不是扫 .db 文件：
    WAL 模式下已删除的字节仍留在文件里，扫文件既证不了落盘、
    也会因为历史残留给出假警报。查不到，才是这里要的答案。
    """
    with database.get_db() as c:
        tables = [
            r[0] for r in c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            columns = [r[1] for r in c.execute(f'PRAGMA table_info("{table}")')]
            for column in columns:
                sql = f'SELECT "{column}" FROM "{table}"'
                for row in c.execute(sql):
                    yield table, column, row[0]


def assert_nothing_stored_says(sentinel):
    """把库里每一列的每一个值都搜一遍。

    逐条报出「哪张表的哪一列存着它」——只报一个总数的话，
    真出了事也不知道该去哪儿看。
    """
    hits = [f"{t}.{c}" for t, c, v in every_stored_value() if v is not None and sentinel in str(v)]
    assert hits == [], f"凭据落进了这些列：{hits}"


# ── AC1：沿用现有流式响应，交互与平台路径一致 ───────────────

class TestSameStreamShape:
    def test_event_sequence_is_identical(self, db, make_user, app, fake_openai):
        """带凭据与不带凭据，事件序列必须逐个同形。"""
        seed_community_video()
        with_key = post_chat(app, make_user("a@example.com"), api_key=SENTINEL)
        without_key = post_chat(app, make_user("b@example.com"))

        assert with_key.status_code == 200, with_key.text
        assert event_list(with_key.text) == event_list(without_key.text) == ["quota", "answer", "done"]

    def test_answer_reaches_the_client(self, db, make_user, app, fake_openai):
        seed_community_video()
        r = post_chat(app, make_user(), api_key=SENTINEL)

        assert event_list(r.text) == ["quota", "answer", "done"]
        answers = [d for n, d in event_pairs(r.text) if n == "answer"]
        assert answers == ["答案片段"], f"带凭据的追问没把答案吐出来：{answers}"

    def test_model_receives_exactly_the_same_messages(self, db, make_user, app, fake_openai):
        """两条路径送给模型的 messages 必须逐字相同。

        两个不同的用户、同一份字幕、都没有历史——所以 messages 只可能因
        「凭据影响了入参」而不同。这比「消息里含字幕」这类弱断言强得多。
        """
        seed_community_video()
        post_chat(app, make_user("a@example.com"), api_key=SENTINEL)
        post_chat(app, make_user("b@example.com"))

        assert len(fake_openai.calls) == 2
        assert messages_of(fake_openai.calls[0]) == messages_of(fake_openai.calls[1])

    def test_user_key_reaches_the_model_client(self, db, make_user, app, fake_openai):
        """凭据必须真的被用上：模型客户端拿到的是用户那把 key，不是平台的。"""
        seed_community_video()
        post_chat(app, make_user(), api_key=SENTINEL)

        assert len(fake_openai.calls) == 1
        api_key, _base_url, kwargs = fake_openai.calls[0]
        assert api_key == SENTINEL, "用户填的 key 没有到达模型客户端"
        assert api_key != PLATFORM_KEY, "带凭据的请求仍在用平台 key"
        assert kwargs["model"], "没有指定模型"

    def test_byok_quota_event_reports_no_consumption(self, db, make_user, app, fake_openai):
        """自带凭据时额度**没有数字可报**（服务端刻意没查），只报没消耗。

        报一个凭空编出来的余额比不报更糟：用户会拿它对照 /api/quota 里的真数字。
        """
        seed_community_video()
        r = post_chat(app, make_user(), api_key=SENTINEL)

        assert quota_event(r.text) == {"byok": True, "consumed": False}

    def test_quota_event_still_carries_numbers_without_credential(self, db, make_user, app, fake_openai):
        seed_community_video()
        payload = quota_event(post_chat(app, make_user()).text)

        assert payload["chat"]["remaining"] == database.DAILY_CHAT_LIMIT - 1


# ── AC2：带凭据的请求不消耗对话额度 ────────────────────────

class TestQuotaNotConsumed:
    def test_counter_unchanged_with_credential(self, db, make_user, app, fake_openai):
        seed_community_video()
        uid = make_user()
        before = chat_count_of(uid)

        post_chat(app, uid, api_key=SENTINEL)

        assert chat_count_of(uid) == before, "带凭据的追问仍然扣了对话额度"

    def test_remaining_drops_by_one_without_credential(self, db, make_user, app, fake_openai):
        """反向对照：没有凭据时必须照扣。

        只测前一条的话，「额度检查整个坏掉、谁都不扣了」也能绿。

        两个数一起断言，因为它们说的不是一回事：
        daily_chat_count 记的是**已用次数**（消耗时 +1），
        用户看到的 remaining 记的是**还剩几次**（消耗时 −1）。
        只查其中一个，「扣了但显示没扣」这类坏掉都抓不住。
        """
        seed_community_video()
        uid = make_user()
        before = chat_count_of(uid)
        remaining_before = database.check_quota_kind(uid, "chat")[1]

        post_chat(app, uid)

        assert chat_count_of(uid) == before + 1, "不带凭据的追问没有消耗对话额度"
        remaining_after = database.check_quota_kind(uid, "chat")[1]
        assert remaining_after == remaining_before - 1

    def test_still_works_when_quota_is_exhausted(self, db, make_user, app, fake_openai):
        """额度用光 + 带凭据 → 照常回答。

        这条最容易在重构时被「统一在函数入口做额度检查」盖掉，
        所以必须单独测：它与「不扣额度」是两件事，扣不扣是记账，放不放行是准入。
        """
        seed_community_video()
        uid = make_user()
        exhaust_chat_quota(uid)

        r = post_chat(app, uid, api_key=SENTINEL)

        assert event_list(r.text) == ["quota", "answer", "done"], f"额度用完就放行了：{r.text}"
        assert chat_count_of(uid) == database.DAILY_CHAT_LIMIT, "额度耗尽时仍不该再扣（会扣成负数）"

    def test_exhausted_quota_still_blocks_the_platform_path(self, db, make_user, app, fake_openai):
        """前提自检：额度真的用光了。不带凭据时必须被拒。"""
        seed_community_video()
        uid = make_user()
        exhaust_chat_quota(uid)

        r = post_chat(app, uid)

        assert event_list(r.text) == ["error"], f"额度耗尽却放行了平台路径：{r.text}"
        assert chat_count_of(uid) == database.DAILY_CHAT_LIMIT

    def test_anonymous_credential_request_is_refused(self, db, make_user, app, fake_openai):
        """仍要登录：额度豁免不是匿名可用的。"""
        seed_community_video()
        uid = make_user()
        exhaust_chat_quota(uid)

        with make_client(app) as c:
            r = c.post("/api/chat", json={"url": URL, "question": "问题",
                                          "user_api_key": SENTINEL})

        assert event_list(r.text) == ["error"]
        assert SENTINEL not in r.text
        assert fake_openai.calls == [], "未登录的请求也调了模型"
        assert chat_count_of(uid) == database.DAILY_CHAT_LIMIT


# ── AC4 / AC5：凭据无效时的固定文案 + 三件套断言 ─────────────

class TestInvalidCredentialLeaksNothing:
    def test_response_carries_a_readable_message_and_no_key(
        self, db, make_user, app, fake_openai, caplog, capfd
    ):
        """凭据无效时用户要看到能自己修正的话，且这句话里没有凭据。"""
        seed_community_video()
        fake_openai.raises = ProviderAuthError(SENTINEL)

        r = post_chat(app, make_user(), api_key=SENTINEL)

        errors = [d for n, d in event_pairs(r.text) if n == "error"]
        assert errors, f"凭据无效却没有报错事件：{event_list(r.text)}"
        assert errors[0]["message"], "错误提示是空的，用户没法自己修正"
        assert SENTINEL not in r.text, "响应体里回显了凭据"

    def test_three_piece_assertion_suite(self, db, make_user, app, fake_openai, caplog, capfd):
        """票面点名的三件套，缺一不可。

        标准错误那条用 capfd 而不是 capsys：capsys 只接得住**当前** sys.stdout /
        sys.stderr 对象上的写，而落败的日志器（lastResort）持有的是 logging
        导入时那个 stream 对象。capfd 在文件描述符层拦，任何绕过 sys 对象的
        写都跑不掉，是 capsys 的超集。
        """
        caplog.set_level(1)  # 任何等级都算——WARNING 级以下的泄漏也算泄漏
        seed_community_video()
        fake_openai.raises = ProviderAuthError(SENTINEL)

        r = post_chat(app, make_user(), api_key=SENTINEL)

        captured = capfd.readouterr()

        # 「日志记录列表为空」这半条按**本项目的日志器**来判。
        # 整个列表做不到字面上的空：pytest 自己就会产生记录——
        # httpx 的 'HTTP Request: POST ... 200 OK' 与 asyncio 的
        # 'Using proactor'。那是测试装置的噪声，不是本项目的日志，
        # 把它算成「有记录」会让这条断言只剩噪声。
        ours = [r for r in caplog.records if r.name.split(".")[0] in APP_LOGGERS]
        assert ours == [], f"这次请求留下了本项目的日志记录：{ours}"
        # 兜底：不管是谁记的，所有记录里都搜不到凭据。
        assert SENTINEL not in caplog.text, f"日志里有凭据：{caplog.text}"
        assert captured.out == "", f"凭据或异常文本进了标准输出：{captured.out!r}"
        assert captured.err == "", f"凭据或异常文本进了标准错误：{captured.err!r}"
        assert SENTINEL not in r.text, "响应体里回显了凭据"

    def test_quota_is_refunded_on_failure(self, db, make_user, app, fake_openai):
        """凭据无效的失败路径：额度本来就没扣，更不该因为失败反而多扣或多退。"""
        seed_community_video()
        uid = make_user()
        fake_openai.raises = ProviderAuthError(SENTINEL)

        post_chat(app, uid, api_key=SENTINEL)

        assert chat_count_of(uid) == 0

    def test_exception_object_itself_is_never_returned(self, db, make_user, app, fake_openai):
        """异常对象本身也没被原样序列化——只查文案不够，它可能从别的字段漏出去。"""
        seed_community_video()
        fake_openai.raises = ProviderAuthError(SENTINEL)

        r = post_chat(app, make_user(), api_key=SENTINEL)

        payload = json.dumps([d for _, d in event_pairs(r.text)])
        assert SENTINEL not in payload
        assert "401" not in payload, "异常原文（含状态码）被回显了"


# ── AC4：凭据不出现在数据库里 ──────────────────────────────

class TestNeverPersisted:
    def test_successful_byok_chat_stores_no_key(self, db, make_user, app, fake_openai):
        seed_community_video()
        r = post_chat(app, make_user(), api_key=SENTINEL)

        assert event_list(r.text) == ["quota", "answer", "done"], "请求本身没成功，这题就白问了"
        assert_nothing_stored_says(SENTINEL)

    def test_failed_byok_chat_stores_no_key(self, db, make_user, app, fake_openai):
        seed_community_video()
        fake_openai.raises = ProviderAuthError(SENTINEL)

        post_chat(app, make_user(), api_key=SENTINEL)

        assert_nothing_stored_says(SENTINEL)

    def test_sweep_actually_walks_the_tables(self, db, make_user, app, fake_openai):
        """自检：扫描器真的能看见库里存的东西。

        一个永远扫不到东西的断言与没有断言等价——所以先往库里写一条**已知**
        含哨兵的行，确认扫描器抓得到，再谈它抓不到别的。
        """
        seed_community_video()
        with database.get_db() as c:
            c.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, 'h')", (SENTINEL,)
            )

        with pytest.raises(AssertionError):
            assert_nothing_stored_says(SENTINEL)


# ── 追问会话（工单 #8）不被自带凭据打断 ──────────────────────

class TestChatSessionStillWorks:
    def test_byok_answer_is_still_recorded(self, db, make_user, app, fake_openai):
        seed_community_video()
        uid = make_user()

        post_chat(app, uid, question="讲了什么", api_key=SENTINEL)

        with database.get_db() as c:
            rows = [
                dict(r) for r in c.execute(
                    "SELECT user_id, role, content FROM chat_messages ORDER BY id"
                )
            ]
        assert [(r["role"], r["content"]) for r in rows] == [
            ("user", "讲了什么"), ("assistant", "答案片段")
        ], "自带凭据的追问没有落进会话记录"
        assert rows[0]["user_id"] == uid
