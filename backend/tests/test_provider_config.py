"""provider 配置抽象的离线测试（工单 #107 / ADR 0028）。

**不联网、不花钱**：OpenAI 兼容协议用替身工厂，Anthropic 协议用替身传输。

断言的全是**外部可观察行为**：请求发到哪个 URL、带哪些头、请求体长什么样、
错误是什么类型、增量文本长什么样。实现细节（内部函数怎么分）不在这里断言。
"""

import json
from types import SimpleNamespace

import pytest

import provider_config


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
    """HTTP 响应的替身。``text`` 是响应体，``iter_lines`` 供 SSE 读取。"""

    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self.text.splitlines())


@pytest.fixture()
def anthropic_calls(monkeypatch):
    """把 Anthropic 协议的 HTTP 通道换成替身，顺带记下它收到什么。"""
    seen = {}

    def transport(url, payload, headers, timeout):
        seen.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return _FakeResponse(text=seen.get("body", ""))

    monkeypatch.setattr(provider_config, "requests_transport", transport)
    return seen


def _sse(*texts):
    """拼一条 Anthropic 风格的 SSE 流。"""
    lines = []
    for text in texts:
        lines.append("event: content_block_delta")
        lines.append("data: " + json.dumps({
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }))
    lines.append("event: message_stop")
    lines.append('data: {"type": "message_stop"}')
    return "\n".join(lines)


# ── 协议归一 ────────────────────────────────────────────────

def test_unknown_protocol_is_rejected():
    """协议名不认识就报错——不猜。

    猜错的后果是把请求发到错误的端点上，拿到一个与真实原因无关的报错。
    """
    with pytest.raises(provider_config.ProviderConfigError) as excinfo:
        provider_config.normalize_protocol("gemini")

    assert "gemini" in str(excinfo.value)
    assert "openai" in str(excinfo.value)
    assert "anthropic" in str(excinfo.value)


def test_protocol_aliases_are_normalized():
    assert provider_config.normalize_protocol("") == provider_config.PROTOCOL_OPENAI
    assert provider_config.normalize_protocol(None) == provider_config.PROTOCOL_OPENAI
    assert provider_config.normalize_protocol(" OpenAI-Compatible ") == (
        provider_config.PROTOCOL_OPENAI
    )
    assert provider_config.normalize_protocol("claude") == (
        provider_config.PROTOCOL_ANTHROPIC
    )


# ── OpenAI 兼容协议 ─────────────────────────────────────────

def test_openai_protocol_sends_thinking_disabled_on_every_request():
    """每次请求都带上 ``enable_thinking: False``。

    该模型**默认开着**思考，不显式关掉的话思维链会先吃掉 max_tokens 的预算
    （同一问句实测 845 vs 47 tokens），长总结于是提前截断。

    这个字段是**请求体级**的：OpenAI SDK 的客户端构造函数不收 ``extra_body``，
    只有 ``create()`` 收。所以断言落在请求参数上，而不是构造参数上。
    """
    fake = _FakeOpenAI()

    def factory(**kwargs):
        fake.init_kwargs.update(kwargs)
        return fake

    settings = provider_config.ProviderSettings(
        provider_id="bailian",
        protocol="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-flash",
        api_key="sk-test-placeholder-not-real",
        enable_thinking=False,
    )
    client = provider_config.build_client(settings, openai_factory=factory)
    client.chat.completions.create(
        model="qwen3.7-flash",
        messages=[{"role": "user", "content": "你好"}],
    )

    assert fake.init_kwargs["api_key"] == "sk-test-placeholder-not-real"
    assert fake.init_kwargs["base_url"] == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert fake.calls[0]["extra_body"] == {"enable_thinking": False}


def test_openai_protocol_without_the_thinking_flag_sends_no_extra_body():
    """反向对照：没有配思考开关时**不要**多发这个字段。

    换成不认 ``enable_thinking`` 的 OpenAI 兼容端点时，多发一个字段就是 400。
    """
    fake = _FakeOpenAI()

    settings = provider_config.ProviderSettings(
        provider_id="custom",
        protocol="openai",
        base_url="https://example.invalid/v1",
        model="some-model",
        api_key="sk-test-placeholder-not-real",
        enable_thinking=None,
    )
    client = provider_config.build_client(
        settings, openai_factory=lambda **kwargs: fake
    )
    client.chat.completions.create(
        model="some-model",
        messages=[{"role": "user", "content": "你好"}],
    )

    assert "extra_body" not in fake.calls[0], (
        f"不该多发这个字段：{fake.calls[0]!r}"
    )


# ── Anthropic Messages API ──────────────────────────────────

def test_anthropic_protocol_posts_to_the_messages_api_with_the_required_headers(
    anthropic_calls,
):
    """Anthropic 的请求形状：URL、头、请求体都跟 OpenAI 兼容协议不一样。"""
    settings = provider_config.ProviderSettings(
        provider_id="anthropic-demo",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4",
        api_key="sk-ant-test-placeholder",
    )
    client = provider_config.build_client(settings)

    client.chat.completions.create(
        model="claude-sonnet-4",
        messages=[
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ],
        stream=True,
        temperature=0.4,
        max_tokens=8192,
    )

    assert anthropic_calls["url"] == "https://api.anthropic.com/v1/messages"
    assert anthropic_calls["headers"]["x-api-key"] == "sk-ant-test-placeholder"
    assert anthropic_calls["headers"]["anthropic-version"] == (
        provider_config.ANTHROPIC_VERSION
    )
    payload = anthropic_calls["payload"]
    # system 提示在顶层，不在 messages 里——放错位置 Anthropic 会 400
    assert payload["system"] == "你是助手"
    assert payload["messages"] == [{"role": "user", "content": "你好"}]
    assert payload["max_tokens"] == 8192
    assert payload["temperature"] == 0.4
    assert payload["stream"] is True


def test_anthropic_base_url_ending_with_v1_is_not_doubled(anthropic_calls):
    """端点写成 ``.../v1`` 时请求路径仍是 ``/v1/messages``。"""
    settings = provider_config.ProviderSettings(
        provider_id="anthropic-demo",
        protocol="anthropic",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4",
        api_key="sk-ant-test-placeholder",
    )
    provider_config.build_client(settings).chat.completions.create(
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "你好"}],
    )

    assert anthropic_calls["url"] == "https://api.anthropic.com/v1/messages"


def test_anthropic_stream_yields_incremental_text_in_the_openai_chunk_shape(
    anthropic_calls,
):
    """适配层给上游的形状必须与 OpenAI 客户端一致。

    上游那段读取代码只认 ``choices[0].delta.content``；形状不一致的话，
    协议差异就会漏到别的模块里去。
    """
    anthropic_calls["body"] = _sse("你好", "世界")
    settings = provider_config.ProviderSettings(
        provider_id="anthropic-demo",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4",
        api_key="sk-ant-test-placeholder",
    )
    client = provider_config.build_client(settings)

    chunks = list(client.chat.completions.create(
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "你好"}],
        stream=True,
    ))

    assert [chunk.choices[0].delta.content for chunk in chunks] == ["你好", "世界"]


def test_anthropic_error_status_is_reported_as_a_provider_error(monkeypatch):
    """provider 报错时**不兜底**：错误直接冒出来，状态码看得见。"""
    def transport(url, payload, headers, timeout):
        return _FakeResponse(status_code=401, text="invalid api key")

    monkeypatch.setattr(provider_config, "requests_transport", transport)
    settings = provider_config.ProviderSettings(
        provider_id="anthropic-demo",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4",
        api_key="sk-ant-test-placeholder",
    )
    client = provider_config.build_client(settings)

    with pytest.raises(provider_config.ProviderRequestError) as excinfo:
        client.chat.completions.create(
            model="claude-sonnet-4",
            messages=[{"role": "user", "content": "你好"}],
        )

    assert "401" in str(excinfo.value)


def test_anthropic_protocol_ignores_the_openai_only_thinking_flag(anthropic_calls):
    """反向对照：``enable_thinking`` 是 OpenAI 兼容协议的字段，不往 Anthropic 送。

    Anthropic 的思考开关是它自己的 ``thinking`` 字段，形状不一样，
    硬塞过去会被 400。
    """
    settings = provider_config.ProviderSettings(
        provider_id="anthropic-demo",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4",
        api_key="sk-ant-test-placeholder",
        enable_thinking=False,
    )
    provider_config.build_client(settings).chat.completions.create(
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "你好"}],
    )

    assert "thinking" not in anthropic_calls["payload"]
    assert "enable_thinking" not in anthropic_calls["payload"]
