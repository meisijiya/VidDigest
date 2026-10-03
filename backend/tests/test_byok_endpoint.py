"""自带凭据的端点覆盖：base_url / model 可指定，且 http 被有意放行。

ADR 0004 定了「服务端转发但不落盘」。这个文件往前推一步：转发到**哪里**
也由用户说了算。理由是自带密钥最有价值的场景恰好是自建 / 本地推理服务
（Ollama、vLLM、LM Studio），它们只监听 http——强制 HTTPS 等于把最需要
这功能的人挡在门外。

于是校验的重点不是「白名单」（用户自建服务无法被提前枚举），而是三条
硬边界，它们各自对应一个具体的坏结果：

- 非 http/https 的协议 → SDK 照着它去读本地文件或内网协议
- 内嵌 userinfo      → 凭据进 URL（进日志、进代理），并与 Authorization 头打架
- 用户填的端点进了 repr → 「打印路径结构性拿不到真值」这条保证被毁掉

最后一条是本文件最该被记住的：端点**不是凭据**，但它是**用户可控的
任意字符串**。把凭据写进 query（``?key=sk-xxx``）比写进 userinfo 更常见，
而我们只拒绝 userinfo——所以端点必须和 api_key 一样，从 repr 里消失。

走真实 HTTP 层（seams.make_client）与真实 VideoSummarizer：
断言落在「送到模型客户端的那份入参」上，而不是内部函数返回值。
"""
import json
from types import SimpleNamespace

import pytest

import api_summarize
import auth
import database
import summarizer
from credentials import CredentialError, UserCredential
from seams import StubExtractor, auth_headers, make_client

URL = "https://example.com/v/byok-endpoint"
OTHER_URL = "https://example.com/v/byok-endpoint-2"

#: 只在这个文件里出现的哨兵。
SENTINEL_KEY = "sk-agent-endpoint-4b7e2a-ZZUNIQUEZZ"

#: 平台侧的假端点与假模型名。**写成绝对值**，不从
#: ``VideoSummarizer.DEFAULT_*`` 推导：推导出来的期望值在「默认值被改错」
#: 时会跟着一起错，测试于是照绿——而线上早已指向别处。
#:
#: 端点与模型名也必须钉住：backend/.env 里有真实值，只换 api_key 的话
#: 「不填端点时回落到平台端点」这条会拿到部署环境的真地址。
PLATFORM_KEY = "platform-key-for-test-only"
PLATFORM_BASE_URL = "https://platform.example.test/compatible-mode/v1"
PLATFORM_MODEL = "platform-model-test-only"

#: 假客户端收到的第一个 chunk 必须先给一段正文，再给哨兵 + JSON，
#: 否则 parse_dual_output 产不出 mindmap / tags，测试会误以为落库失败。
BODY_CHUNKS = (
    "这是总结正文。",
    "<<<VIDDIGEST_MINDMAP_TAGS>>>",
    json.dumps({"mindmap": "# 主题\n## 章节", "tags": ["编程"]}, ensure_ascii=False),
)


class ProviderError(RuntimeError):
    """模拟第三方 SDK 把凭据回显进异常文本与属性。"""

    def __init__(self, key):
        super().__init__(f"401 Error: incorrect API key provided: {key}")
        self.request_headers = {"Authorization": f"Bearer {key}"}


class FakeOpenAI:
    """替换 openai 客户端：如实记下 api_key / base_url / 每次调用的 model。"""

    calls = []
    raises = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        FakeOpenAI.calls.append({
            "api_key": self.kwargs.get("api_key"),
            "base_url": self.kwargs.get("base_url"),
            "model": kwargs.get("model"),
        })
        if FakeOpenAI.raises is not None:
            raise FakeOpenAI.raises
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])
            for t in BODY_CHUNKS
        ]


@pytest.fixture()
def fake_openai(monkeypatch):
    FakeOpenAI.calls = []
    FakeOpenAI.raises = None
    monkeypatch.setattr(summarizer, "OpenAI", FakeOpenAI)
    # 平台侧三项全部换成假值：否则不带凭据的那条路径会拿 .env 里的真 key
    # 建客户端——即使不发请求，那也是把真凭据带进测试进程。
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", PLATFORM_KEY)
    monkeypatch.setenv("ALIYUN_BAILIAN_BASE_URL", PLATFORM_BASE_URL)
    monkeypatch.setenv("ALIYUN_BAILIAN_MODEL", PLATFORM_MODEL)
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: StubExtractor())
    return FakeOpenAI


@pytest.fixture()
def app(db):
    import main as main_module
    return main_module.app


# ── 工具 ──────────────────────────────────────────────────

def post_chat(app, uid, api_key=None, base_url=None, model=None, email="a@example.com"):
    payload = {"url": URL, "question": "问题"}
    if api_key is not None:
        payload["user_api_key"] = api_key
    if base_url is not None:
        payload["base_url"] = base_url
    if model is not None:
        payload["model"] = model
    with make_client(app) as c:
        return c.post("/api/chat", json=payload,
                      headers=auth_headers(auth.create_token(uid, email)))


def post_summarize(app, uid, api_key=None, base_url=None, model=None, url=URL,
                   email="a@example.com"):
    payload = {"url": url}
    if api_key is not None:
        payload["user_api_key"] = api_key
    if base_url is not None:
        payload["base_url"] = base_url
    if model is not None:
        payload["model"] = model
    with make_client(app) as c:
        return c.post("/api/summarize", json=payload,
                      headers=auth_headers(auth.create_token(uid, email)))


def event_pairs(response_text):
    lines = response_text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.startswith("event: "):
            raw = lines[i + 1][6:] if i + 1 < len(lines) and lines[i + 1].startswith("data: ") else ""
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                payload = raw
            out.append((line[7:], payload))
    return out


def parse_count(db, uid):
    with db.get_db() as c:
        return c.execute(
            "SELECT daily_parse_count FROM users WHERE id = ?", (uid,)
        ).fetchone()[0]


# ── 端点真的到达模型客户端 ────────────────────────────────

class TestEndpointReachesTheModelClient:
    def test_custom_base_url_and_model_are_used(self, app, db, make_user, fake_openai):
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY,
                      base_url="https://api.example.com/v1", model="my-model-x")

        assert r.status_code == 200
        assert fake_openai.calls, "模型客户端根本没被构造"
        call = fake_openai.calls[0]
        assert call["base_url"] == "https://api.example.com/v1", call
        assert call["model"] == "my-model-x", call
        assert call["api_key"] == SENTINEL_KEY

    def test_plain_http_endpoint_is_accepted(self, app, db, make_user, fake_openai):
        """http 必须放行——自建推理服务只监听 http。

        判据是「真的构造出了客户端且 base_url 原样到达」，
        而不是「没报错」：一个把 http 静默降级成 https 的实现
        同样不会报错，但用户会连不上自己的机器。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY,
                      base_url="http://localhost:11434/v1")

        assert r.status_code == 200, r.text
        assert fake_openai.calls[0]["base_url"] == "http://localhost:11434/v1"

    def test_trailing_slash_is_normalised_away(self, app, db, make_user, fake_openai):
        """尾斜杠会让 SDK 拼出 ``.../v1//chat/completions``。

        断言的是**被归一**后的值：留着尾斜杠也能跑通大多数服务端，
        所以「跑通了」证明不了归一发生过。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        post_chat(app, uid, api_key=SENTINEL_KEY, base_url="https://api.example.com/v1/")
        assert fake_openai.calls[0]["base_url"] == "https://api.example.com/v1"

    def test_omitted_endpoint_falls_back_to_the_platform_one(
        self, app, db, make_user, fake_openai
    ):
        """不填端点时行为与从前完全一致——不能因为多了两个字段就漂移。"""
        uid = db.create_user("a@example.com", "h")["id"]
        post_chat(app, uid, api_key=SENTINEL_KEY)
        call = fake_openai.calls[0]
        assert call["base_url"] == PLATFORM_BASE_URL, call
        assert call["model"] == PLATFORM_MODEL, call

    def test_endpoint_without_a_key_counts_as_no_credential(
        self, app, db, make_user, fake_openai
    ):
        """只填端点没填 key = 配不起来，当成「没填」。

        判据是**用了平台的 key 与端点**：当成「配置错误」而报错，
        会对一个只想要「我填漏了」的用户说一堆他听不懂的话。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, base_url="https://api.example.com/v1")

        assert r.status_code == 200, r.text
        call = fake_openai.calls[0]
        assert call["api_key"] == PLATFORM_KEY, call
        assert call["base_url"] == PLATFORM_BASE_URL, (
            f"只填了端点就被采用了：{call}"
        )


# ── 硬边界 ────────────────────────────────────────────────

class TestRejectedEndpoints:
    @pytest.mark.parametrize("bad", [
        "file:///etc/passwd",
        "ftp://example.com/v1",
        "gopher://example.com",
        "javascript:alert(1)",
    ])
    def test_non_http_schemes_are_refused(self, app, db, make_user, fake_openai, bad):
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY, base_url=bad)

        assert r.status_code == 200, r.text
        assert event_pairs(r.text)[0][0] == "error", r.text
        assert fake_openai.calls == [], "被拒的端点仍然建出了客户端"

    @pytest.mark.parametrize("bad", [
        "https://user:pass@api.example.com/v1",
        "https://sk-leaked@api.example.com/v1",
    ])
    def test_embedded_userinfo_is_refused(self, app, db, make_user, fake_openai, bad):
        """内嵌 userinfo 既是「凭据进 URL」的路，也会与 Authorization 头打架。"""
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY, base_url=bad)

        assert event_pairs(r.text)[0][0] == "error", r.text
        assert fake_openai.calls == []

    def test_missing_host_is_refused(self, app, db, make_user, fake_openai):
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY, base_url="https:///v1")
        assert event_pairs(r.text)[0][0] == "error", r.text

    def test_rejection_message_never_echoes_what_the_user_typed(
        self, app, db, make_user, fake_openai
    ):
        """错误消息里不能出现用户填的原文。

        原文可能本身就是一个被误粘进来的密钥——抄进响应就等于
        把它送回浏览器历史与代理日志。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY,
                      base_url="https://sk-LEAKED-abc@api.example.com/v1")

        assert SENTINEL_KEY not in r.text
        assert "sk-LEAKED-abc" not in r.text, f"回显了用户填的原文：{r.text[:400]}"
        assert "api.example.com" not in r.text

    def test_oversized_endpoint_is_refused(self, app, db, make_user, fake_openai):
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY,
                      base_url="https://api.example.com/v1?k=" + "x" * 800)
        assert event_pairs(r.text)[0][0] == "error", r.text

    def test_rejection_happens_before_anything_is_spent(
        self, app, db, make_user, fake_openai
    ):
        """拒绝不能留下占位——否则这个链接会卡到超时。"""
        uid = db.create_user("a@example.com", "h")["id"]
        before = parse_count(db, uid)

        r = post_summarize(app, uid, api_key=SENTINEL_KEY,
                           base_url="file:///etc/passwd", url=OTHER_URL)

        assert event_pairs(r.text)[0][0] == "error", r.text
        assert db.get_video_by_url(OTHER_URL) is None, "被拒的请求留下了占位"
        assert parse_count(db, uid) == before
        assert fake_openai.calls == []

    def test_control_characters_in_model_are_refused(
        self, app, db, make_user, fake_openai
    ):
        """控制字符能进日志、能拼进 header。"""
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_chat(app, uid, api_key=SENTINEL_KEY, model="bad\nmodel")
        assert event_pairs(r.text)[0][0] == "error", r.text
        assert fake_openai.calls == []


# ── 端点不进 repr（结构性保证）────────────────────────────

class TestEndpointNeverReachesARepr:
    def test_repr_is_the_same_mask_whatever_the_endpoint(
        self, app, db, make_user, fake_openai
    ):
        """端点不是凭据，但它是**用户可控的任意字符串**。

        把凭据写进 query（``?key=sk-xxx``）比写进 userinfo 更常见，
        而我们只拒绝 userinfo。所以端点必须和 api_key 一样从 repr 里消失——
        否则「任何打印路径都拿不到用户填的东西」这条保证就没了。
        """
        plain = UserCredential("sk-a", "", "")
        custom = UserCredential(
            "sk-a", "https://api.example.com/v1?key=sk-LEAKED", "my-model",
        )
        assert repr(plain) == repr(custom), "repr 随端点变化了"
        assert "sk-LEAKED" not in repr(custom)
        assert "api.example.com" not in repr(custom)
        assert "my-model" not in repr(custom)

    def test_the_endpoint_is_still_reachable_on_purpose(self):
        """遮蔽不是「拿不到」——诊断要用它，且只能显式取。"""
        cred = UserCredential("sk-a", "https://api.example.com/v1", "m")
        assert cred.endpoint == ("https://api.example.com/v1", "m")

    def test_an_endpoint_containing_the_key_never_leaks_into_logs(
        self, app, db, make_user, fake_openai, caplog
    ):
        """端点被拒时，日志里也不能出现用户填的原文。"""
        uid = db.create_user("a@example.com", "h")["id"]
        with caplog.at_level("DEBUG"):
            r = post_chat(app, uid, api_key=SENTINEL_KEY,
                          base_url="https://sk-LEAKED@api.example.com/v1")
        assert "sk-LEAKED" not in caplog.text, caplog.text
        assert SENTINEL_KEY not in caplog.text


# ── 解析路径：BYOK 生成总结并进社区 ───────────────────────

class TestByokSummarizeSharesIntoTheCommunity:
    def test_byok_summary_costs_no_quota_and_lands_in_the_community(
        self, app, db, make_user, fake_openai
    ):
        """自付费的总结同样进社区共享（用户已选）。

        判据落在两处：额度**没动**，以及社区表里真的有了那一行。
        只验额度不验落库，会漏掉「结果只留在浏览器里、别人看不到」。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        before = parse_count(db, uid)

        r = post_summarize(app, uid, api_key=SENTINEL_KEY,
                           base_url="https://api.example.com/v1", model="my-model-x")

        assert event_pairs(r.text)[-1][0] == "done", r.text
        assert parse_count(db, uid) == before, "自带凭据仍然扣了额度"
        row = db.get_video_by_url(URL)
        assert row is not None and row["status"] == "ready", "结果没有进社区"
        assert row["summary_md"], "落库的是空的"
        assert row["parsed_by"] == uid
        assert fake_openai.calls[0]["base_url"] == "https://api.example.com/v1"
        assert fake_openai.calls[0]["model"] == "my-model-x"

    def test_byok_quota_event_carries_no_balance_just_the_marker(
        self, app, db, make_user, fake_openai
    ):
        """自带凭据时额度事件**只**带 byok 标记，不带任何余额。

        两个错法都要挡住：
        - 完全不发 → 前端无从知道「这次没扣」，用户以为白花了额度
        - 报一个余额 → 上面刻意没查过库，那个数字是编的
        """
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_summarize(app, uid, api_key=SENTINEL_KEY)
        quotas = [p for name, p in event_pairs(r.text) if name == "quota"]

        assert len(quotas) == 1, event_pairs(r.text)
        assert quotas[0] == {"byok": True, "consumed": False}, quotas[0]

    def test_the_platform_path_still_reports_a_real_balance(
        self, app, db, make_user, fake_openai
    ):
        """反面对照：平台路径必须照旧报真余额。

        没有这一条，一个「永远发 byok 标记」的实现能让上一条变绿，
        而用户从此再也看不到自己还剩几次。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        r = post_summarize(app, uid)
        quotas = [p for name, p in event_pairs(r.text) if name == "quota"]

        assert len(quotas) == 1
        assert quotas[0].get("byok") is None, quotas[0]
        assert quotas[0]["parse"]["remaining"] == (
            quotas[0]["parse"]["limit"] - 1
        ), quotas[0]

    def test_provider_failure_never_echoes_the_credential(
        self, app, db, make_user, fake_openai
    ):
        """第三方异常可能带 key 的前若干位，绝不能原样回显。"""
        uid = db.create_user("a@example.com", "h")["id"]
        fake_openai.raises = ProviderError(SENTINEL_KEY)

        r = post_summarize(app, uid, api_key=SENTINEL_KEY)

        assert SENTINEL_KEY not in r.text, f"凭据被回显了：{r.text[:400]}"
        pairs = event_pairs(r.text)
        assert pairs[-1][0] == "error", r.text
        assert pairs[-1][1].get("byok") is True, pairs[-1]
        # 失败不能留下占位
        assert db.get_video_by_url(URL) is None

    def test_platform_path_still_reports_the_real_error(
        self, app, db, make_user, fake_openai
    ):
        """反面对照：不带凭据时仍回显异常。

        没有这一条，「一律吞掉异常」也能让上一条变绿，
        而用户会对着「凭据无效」去排查一个与自己无关的故障。
        """
        uid = db.create_user("a@example.com", "h")["id"]
        fake_openai.raises = RuntimeError("平台侧炸了")

        r = post_summarize(app, uid)

        assert "平台侧炸了" in r.text, r.text
        assert parse_count(db, uid) == 0, "失败的平台调用没有退回额度"
