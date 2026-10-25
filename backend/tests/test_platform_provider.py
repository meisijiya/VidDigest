"""平台 provider 的离线行为（工单 #107 / ADR 0028）。

平台侧的三条口径在这里落成可观察的断言：

1. **模型统一**：清单表里的平台默认模型就是实际用的模型；
2. **没有兜底链**：只配了 DeepSeek 的 key 不算配置好了；
3. **协议可选**：``openai``（默认）/ ``anthropic``（Messages API）。

**不联网、不花钱**：OpenAI 兼容协议用替身客户端，Anthropic 协议用替身传输。
断言的是「请求长什么样」「错误是什么」「读到的文本是什么」，
不测内部函数怎么分。
"""

import json
from types import SimpleNamespace

import pytest

import provider_config
import summarizer


# ── 替身 ────────────────────────────────────────────────────

class _FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        return []


class _FakeOpenAI:
    """OpenAI 兼容客户端的替身：记下构造参数与每次调用的参数。"""

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))


class _FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self.text.splitlines())


def _sse(*texts):
    """拼一条 Anthropic 风格的 SSE 流。"""
    lines = []
    for text in texts:
        lines.append("event: content_block_delta")
        lines.append("data: " + json.dumps({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }))
    return "\n".join(lines)


@pytest.fixture()
def platform_env(monkeypatch):
    """一套干净的「只配了平台百炼 key」的环境。

    每个用例都要显式面对**没有**配置的那些变量：``.env`` 里多一行
    ``ALIYUN_BAILIAN_MODEL`` 就能让「env 优先于表」那条口径整个失守。
    """
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", "sk-test-placeholder-not-real")
    for name in (
        "ALIYUN_BAILIAN_MODEL",
        "ALIYUN_BAILIAN_BASE_URL",
        "ALIYUN_BAILIAN_PROTOCOL",
        "ALIYUN_BAILIAN_ENABLE_THINKING",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ── 模型与参数 ──────────────────────────────────────────────

def test_platform_model_is_the_default_from_the_catalog(db, platform_env):
    """平台用的模型 = 清单表里的平台默认模型（表是唯一真值）。"""
    assert summarizer.VideoSummarizer().model == "qwen3.7-flash"


def test_env_model_still_wins_over_the_catalog(db, platform_env):
    """env 覆盖表——既有优先级不能被这次改动改掉（ADR 0011）。

    这里故意用一个**与表里默认值不同**的模型名：两边填一样的值时，
    这条用例在「表赢」的世界里也是绿的，等于没测。
    """
    platform_env.setenv("ALIYUN_BAILIAN_MODEL", "qwen-turbo")
    assert summarizer.VideoSummarizer().model == "qwen-turbo"


def test_platform_request_disables_thinking(db, platform_env):
    """请求里显式关思考。

    该模型**默认开着**思考：不显式关掉，思维链会先吃掉 max_tokens 的预算
    （同一问句实测 845 vs 47 tokens），长总结于是提前截断。

    ``enable_thinking`` 是**请求体级**字段：OpenAI SDK 的客户端构造函数
    不收 ``extra_body``（实测 openai 3.37.0），只有 ``create()`` 收。
    所以断言落在请求参数上。
    """
    fake = _FakeOpenAI(api_key="sk-test-placeholder-not-real")
    platform_env.setattr(summarizer, "OpenAI", lambda **kwargs: fake)

    list(summarizer.VideoSummarizer().summarize_full_stream("字幕内容"))

    assert len(fake.calls) == 1, fake.calls
    assert fake.calls[0].get("extra_body") == {"enable_thinking": False}, (
        f"请求没有显式关思考：{fake.calls[0]!r}"
    )


def test_summary_request_uses_the_new_parameters(db, platform_env):
    """总结这一路：temperature 0.4、max_tokens 8192。"""
    fake = _FakeOpenAI(api_key="sk-test-placeholder-not-real")
    platform_env.setattr(summarizer, "OpenAI", lambda **kwargs: fake)

    list(summarizer.VideoSummarizer().summarize_full_stream("字幕内容"))

    assert len(fake.calls) == 1, fake.calls
    call = fake.calls[0]
    assert call["temperature"] == 0.4
    assert call["max_tokens"] == 8192


# ── 没有兜底链 ──────────────────────────────────────────────

def test_only_a_deepseek_key_is_not_a_fallback(db, platform_env):
    """只配 DeepSeek 的 key **不**算配置好了（工单 #107 删掉了那段兜底）。

    那段兜底有三个问题：只换模型名不换端点必然 404；兜底会把第一个
    provider 的真实错误盖住；让人以为「配了就能用」。

    这里用 try/except 而不是 ``pytest.raises``：变异脚本要求「转红必须是
    AssertionError」，而 ``pytest.raises`` 没等到异常时报的是
    ``DID NOT RAISE``——那是「变异体自己没按预期坏掉」，不是「断言抓到了
    缺陷」。
    """
    platform_env.delenv("ALIYUN_BAILIAN_API_KEY", raising=False)
    platform_env.setenv("DEEPSEEK_API_KEY", "sk-test-placeholder-not-real")

    raised = None
    try:
        summarizer.VideoSummarizer()
    except ValueError as exc:
        raised = exc

    assert raised is not None, (
        "只配了 DEEPSEEK_API_KEY 竟然构造出了客户端 —— 兜底链没删干净"
    )
    message = str(raised)
    assert "ALIYUN_BAILIAN_API_KEY" in message, message
    assert "DEEPSEEK_API_KEY" not in message, (
        f"错误信息还在教人配那个已经删掉的兜底：{message}"
    )


# ── 协议路由 ────────────────────────────────────────────────

def test_platform_defaults_to_the_openai_protocol(db, platform_env):
    """反向对照：没配协议时走 OpenAI 兼容协议，且**不发**任何 HTTP 请求。"""
    created = {}

    def factory(**kwargs):
        created.update(kwargs)
        return _FakeOpenAI(**kwargs)

    platform_env.setattr(summarizer, "OpenAI", factory)

    def transport(*args, **kwargs):
        raise AssertionError("OpenAI 兼容协议下不该走 HTTP 传输")

    platform_env.setattr(provider_config, "requests_transport", transport)

    summarizer.VideoSummarizer()
    assert created.get("base_url", "").startswith("https://dashscope.aliyuncs.com/"), (
        f"没走 OpenAI 兼容协议：构造参数 {created!r}"
    )


def test_platform_protocol_anthropic_routes_to_the_messages_api(db, platform_env):
    """配成 anthropic 协议时，请求必须发到 Messages API。

    协议路由错了（OpenAI 兼容的配置走到 Anthropic 适配器，或反过来）
    在真实环境里表现为一个看不懂的 404/400，这里必须能离线看出来。
    """
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return _FakeResponse(text=_sse("总结正文"))

    platform_env.setattr(provider_config, "requests_transport", transport)
    platform_env.setenv("ALIYUN_BAILIAN_PROTOCOL", "anthropic")
    platform_env.setenv("ALIYUN_BAILIAN_BASE_URL", "https://api.anthropic.com")

    events = list(summarizer.VideoSummarizer().summarize_full_stream("字幕内容"))

    assert seen.get("url") == "https://api.anthropic.com/v1/messages", (
        f"请求没发到 Messages API：{seen!r}"
    )
    headers = seen.get("headers", {})
    assert headers.get("anthropic-version") == provider_config.ANTHROPIC_VERSION, headers
    assert headers.get("x-api-key") == "sk-test-placeholder-not-real", headers
    assert seen.get("payload", {}).get("model") == "qwen3.7-flash", seen.get("payload")
    assert ("summary", "总结正文") in events, events


def test_unknown_protocol_is_reported_at_startup(db, platform_env):
    """协议名不认识就在构造时报错，别等请求打出去才 404。"""
    platform_env.setenv("ALIYUN_BAILIAN_PROTOCOL", "gemini")

    with pytest.raises(ValueError) as excinfo:
        summarizer.VideoSummarizer()

    assert "gemini" in str(excinfo.value)
