"""provider 配置抽象（工单 #107 / ADR 0028）。

## 为什么有这个模块

平台侧的模型调用此前是**两段硬编码分支**：百炼一段、DeepSeek 一段兜底。
那段兜底有三个具体问题：

1. **只换模型名不换端点必然 404**——DeepSeek 的模型名拿到百炼端点上去，
   端点根本不认识那个模型；
2. **兜底链让失败变得不可见**——第一个 provider 挂了，错误被第二个 provider
   的报文盖住，排查时看到的是一个与真实原因无关的 404；
3. **协议写死在代码里**——换一个说 Anthropic Messages API 的 provider，
   没有插进来的地方。

本模块把「provider」抽象成一份配置：

* :class:`ProviderSettings` —— 协议 / 端点 / 模型 / key；
* :func:`build_client` —— 按协议给出客户端；
* :class:`AnthropicClient` —— 用仓库已有的 ``requests`` 手写的 Anthropic
  Messages API 适配层（**不引入 anthropic SDK**：本仓不装新依赖）。

## 两种协议的形状差别

* ``openai``：OpenAI 兼容协议。端点是**前缀**（``.../compatible-mode/v1``），
  SDK 自己补 ``/chat/completions``。百炼兼容模式、DeepSeek、Moonshot、
  Ollama、自建网关都走这一条。
* ``anthropic``：Anthropic Messages API。端点是**根**
  （``https://api.anthropic.com``），适配层自己补 ``/v1/messages``；
  端点尾部带的 ``/v1`` 会被去掉，补出来的路径因此总是 ``/v1/messages``。

两种协议**对外形状一致**：客户端都提供 ``client.chat.completions.create(...)``，
返回值都可迭代、元素都带 ``choices[0].delta.content``。于是上游那段
``_iter_delta_text`` 一个字都不用改——协议差异被关在这个模块里。

## 凭据不落库

key 只从环境变量进内存（ADR 0004 / 0011）：本模块不接受任何来自数据库或
请求体的 key，也不把它写进任何日志。
"""

import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace

import requests
from openai import OpenAI

logger = logging.getLogger("provider_config")

#: OpenAI 兼容协议。
PROTOCOL_OPENAI = "openai"

#: Anthropic Messages API。
PROTOCOL_ANTHROPIC = "anthropic"

#: 支持的协议全集。
SUPPORTED_PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

#: Anthropic Messages API 的版本头。官方要求带，缺了会 400。
ANTHROPIC_VERSION = "2023-06-01"

#: Anthropic 的 max_tokens 是**必填**字段（OpenAI 那边是可选的）。
#: 兜底值给得小一点：调用方一般自己传，传不到时也不至于一次吐 8k token。
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096

#: 默认请求超时（秒）。
DEFAULT_TIMEOUT = 120.0

#: 协议名的别名。键一律小写。
_PROTOCOL_ALIASES = {
    "openai": PROTOCOL_OPENAI,
    "openai-compatible": PROTOCOL_OPENAI,
    "openai_compatible": PROTOCOL_OPENAI,
    "compatible": PROTOCOL_OPENAI,
    "anthropic": PROTOCOL_ANTHROPIC,
    "claude": PROTOCOL_ANTHROPIC,
}


class ProviderConfigError(ValueError):
    """provider 配置不合法（协议不认识、端点为空、凭据为空……）。"""


class ProviderRequestError(RuntimeError):
    """provider 调用失败（HTTP 4xx/5xx、网络异常）。

    **不做兜底**：错误直接冒到调用方。兜底链会把真实原因盖住，
    而「第一个 provider 挂了」这件事本身就该被看见。
    """


@dataclass(frozen=True)
class ProviderSettings:
    """一个 provider 的全部配置。

    :param provider_id: 厂商标识（错误信息里用）。
    :param protocol: 协议名，见 :data:`SUPPORTED_PROTOCOLS`。
    :param base_url: 端点。OpenAI 兼容协议是前缀，Anthropic 协议是根。
    :param model: 模型名。
    :param api_key: 凭据。**只从环境变量来**。
    :param enable_thinking: ``False`` 显式关思考、``True`` 显式开思考、
        ``None`` **不发**这个参数。只在 OpenAI 兼容协议下有意义——
        ``enable_thinking`` 不是 Anthropic 的字段，发过去会被 400。
    :param timeout: 请求超时（秒）。
    """

    provider_id: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    enable_thinking: bool | None = None
    timeout: float = DEFAULT_TIMEOUT


def normalize_protocol(value: str | None) -> str:
    """把配置里的协议名归一成标准值。

    空值 → OpenAI 兼容协议（本仓历史上唯一的协议）。
    不认识的名字 → :class:`ProviderConfigError`（**不猜**：猜错就是
    把请求发到错误的端点上，报错比猜好）。
    """
    raw = (value or "").strip().lower()
    if not raw:
        return PROTOCOL_OPENAI
    try:
        return _PROTOCOL_ALIASES[raw]
    except KeyError:
        raise ProviderConfigError(
            f"未知的 provider 协议 {value!r}；支持的是："
            f"{'、'.join(SUPPORTED_PROTOCOLS)}"
        ) from None


def build_client(settings: ProviderSettings, openai_factory=None):
    """按协议给出客户端。

    :param openai_factory: OpenAI 兼容客户端的构造函数。默认用 SDK 里的
        ``OpenAI``；测试传自己的替身，于是**不联网**也能断言构造参数。
    """
    protocol = normalize_protocol(settings.protocol)
    if protocol == PROTOCOL_OPENAI:
        return _build_openai_client(settings, openai_factory)
    return _build_anthropic_client(settings)


def _build_openai_client(settings: ProviderSettings, openai_factory=None):
    """OpenAI 兼容客户端。

    ``extra_body``（百炼的 ``enable_thinking``）是**请求体级**的参数：
    OpenAI SDK 的客户端构造函数不收它，只有 ``create()`` 收
    （``extra_body`` 会被并进请求 JSON）。所以这里用
    :class:`OpenAICompatClient` 包一层，把这个 provider 的固定请求体字段
    在**每次调用**时注入——provider 的参数只在这里出现一次。
    """
    factory = openai_factory or OpenAI
    client = factory(api_key=settings.api_key, base_url=settings.base_url)
    extra_body = None
    if settings.enable_thinking is not None:
        # 百炼的扩展参数。该模型的思考**默认开着**，不显式关掉的话思维链
        # 会先吃掉 max_tokens 的预算（同一问句实测 845 vs 47 tokens），
        # 长总结会提前截断。
        extra_body = {"enable_thinking": settings.enable_thinking}
    return OpenAICompatClient(client, extra_body=extra_body)


class OpenAICompatClient:
    """OpenAI 兼容客户端的薄包装：注入 provider 级的固定请求体字段。

    对外形状与 SDK 客户端一致：``client.chat.completions.create(...)``。
    """

    def __init__(self, client, extra_body=None):
        self._client = client
        self._extra_body = dict(extra_body or {})
        self.chat = SimpleNamespace(completions=_OpenAICompatCompletions(self))

    @property
    def raw(self):
        """被包装的 SDK 客户端。需要 SDK 专有接口时从这里拿。"""
        return self._client


class _OpenAICompatCompletions:
    """``client.chat.completions`` 的实现（把固定请求体字段并进去）。"""

    def __init__(self, owner: OpenAICompatClient):
        self._owner = owner

    def create(self, **kwargs):
        extra = self._owner._extra_body
        if extra:
            merged = dict(kwargs.get("extra_body") or {})
            merged.update(extra)
            kwargs["extra_body"] = merged
        return self._owner._client.chat.completions.create(**kwargs)


def _build_anthropic_client(settings: ProviderSettings):
    """Anthropic Messages API 客户端。"""
    return AnthropicClient(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
    )


class AnthropicClient:
    """Anthropic Messages API 的最小适配层（用仓库已有的 ``requests`` 手写）。

    **为什么不装 anthropic SDK**：本仓不引入新依赖，部署镜像里也没有它，
    而 Messages API 用到的就三样东西——一个 POST、两个头、一条 SSE 流。
    为这三样东西拉一整个 SDK 不划算，而且 SDK 升级会带着行为变化进来。

    对外形状与 OpenAI 客户端一致：``client.chat.completions.create(...)``
    返回可迭代对象，元素带 ``choices[0].delta.content``。于是上游那段
    ``_iter_delta_text`` 一个字都不用改。

    :param transport: 发起 HTTP 请求的可调用对象，签名
        ``transport(url, payload, headers, timeout) -> response``。
        默认用 :func:`requests_transport`。测试注入替身，于是**不联网**
        也能断言「provider 到底收到了什么」。
    """

    def __init__(self, api_key, base_url, timeout=DEFAULT_TIMEOUT, transport=None):
        if not (api_key or "").strip():
            raise ProviderConfigError("provider 凭据为空")
        if not (base_url or "").strip():
            raise ProviderConfigError("provider 端点为空")
        self.api_key = api_key.strip()
        self.base_url = _anthropic_root(base_url)
        self.timeout = timeout
        self.transport = transport or requests_transport
        self.chat = SimpleNamespace(completions=_AnthropicCompletions(self))

    def _headers(self, stream: bool) -> dict:
        """请求头。

        Anthropic 用 ``x-api-key`` 而不是 ``Authorization: Bearer``，
        并且要求带 ``anthropic-version``——缺一个都是 400。
        """
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if stream:
            headers["accept"] = "text/event-stream"
        return headers

    def _request(self, payload: dict, stream: bool):
        """发一次请求，并把非 2xx 变成 :class:`ProviderRequestError`。"""
        url = f"{self.base_url}/v1/messages"
        response = self.transport(
            url, payload, self._headers(stream), self.timeout
        )
        status = getattr(response, "status_code", 200)
        if status >= 400:
            raise ProviderRequestError(
                f"provider 返回 {status}：{_response_text(response)[:200]}"
            )
        return response


class _AnthropicCompletions:
    """``client.chat.completions`` 的实现（形状照着 OpenAI 客户端对齐）。"""

    def __init__(self, client: AnthropicClient):
        self._client = client

    def create(self, *, model, messages, stream=False, temperature=None,
               max_tokens=None, **_ignored):
        """发一次 Messages 请求。

        参数名与 OpenAI 客户端一致（``model`` / ``messages`` / ``stream`` /
        ``temperature`` / ``max_tokens``），多出来的键一律忽略——上游那段
        调用代码不必知道协议差在哪。
        """
        payload, system = _to_anthropic_payload(messages)
        payload["model"] = model
        payload["max_tokens"] = int(max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS)
        if system is not None:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        response = self._client._request(payload, bool(stream))
        if stream:
            return _AnthropicStream(response)
        return _AnthropicResponse(response)


class _AnthropicChunk:
    """与 OpenAI 的流式块同形：``choices[0].delta.content``。"""

    def __init__(self, content: str):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


class _AnthropicStream:
    """流式响应：把 SSE 事件摊平成与 OpenAI 一样的增量块。"""

    def __init__(self, response):
        self._response = response

    def __iter__(self):
        for text in _iter_sse_text(self._response):
            yield _AnthropicChunk(text)


class _AnthropicResponse:
    """非流式响应：一次把全部文本交出来。"""

    def __init__(self, response):
        self._response = response

    def __iter__(self):
        for block in _response_json(self._response).get("content") or []:
            text = block.get("text") if isinstance(block, dict) else None
            if text:
                yield _AnthropicChunk(text)


def _anthropic_root(base_url: str) -> str:
    """把 Anthropic 端点归一成**根**。

    两种写法都要接受：``https://api.anthropic.com`` 与
    ``https://api.anthropic.com/v1``。后者去掉尾部的 ``/v1``，
    补出来的请求路径于是总是 ``/v1/messages``，不会变成 ``/v1/v1/messages``。
    """
    root = (base_url or "").strip().rstrip("/")
    if root.lower().endswith("/v1"):
        root = root[: -len("/v1")]
    return root


def _to_anthropic_payload(messages):
    """把 OpenAI 形状的 messages 转成 Anthropic 形状。

    Anthropic 的 system 提示**不在 messages 里**，它在顶层 ``system`` 字段；
    user / assistant 才是 messages。转错了 Anthropic 会 400。

    :return: ``(payload, system)``——payload 里先只有 ``messages``。
    """
    system_parts = []
    converted = []
    for message in messages or []:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        converted.append({"role": role, "content": content or ""})
    system = "\n\n".join(system_parts) if system_parts else None
    return {"messages": converted}, system


def _iter_sse_text(response):
    """从 Anthropic 的 SSE 流里取出增量文本。

    Anthropic 的事件长这样::

        event: content_block_delta
        data: {"type": "content_block_delta",
               "delta": {"type": "text_delta", "text": "你好"}}

    只认 ``content_block_delta`` 里的 ``delta.text``——那才是真正的正文。
    ``message_start`` / ``content_block_stop`` / ``message_delta`` 这些
    事件不带正文，跳过。
    """
    for line in _iter_lines(response):
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "error":
            raise ProviderRequestError(f"provider 返回错误事件：{event.get('error')}")
        if event.get("type") != "content_block_delta":
            continue
        delta = event.get("delta") or {}
        text = delta.get("text") if isinstance(delta, dict) else None
        if text:
            yield text


def _iter_lines(response):
    """按行读响应体。``requests`` 的 ``iter_lines`` 默认吐 bytes。"""
    iter_lines = getattr(response, "iter_lines", None)
    if callable(iter_lines):
        for line in iter_lines():
            yield line.decode("utf-8", "replace") if isinstance(line, bytes) else line
        return
    for line in (getattr(response, "text", "") or "").splitlines():
        yield line


def _response_json(response) -> dict:
    """把响应体读成 dict。读不出来就当空响应。"""
    try:
        payload = response.json()
    except (AttributeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _response_text(response) -> str:
    """把响应体读成文本（错误信息里用）。"""
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


def requests_transport(url, payload, headers, timeout):
    """默认 HTTP 通道：用仓库已有的 ``requests``。

    这里**只管把请求发出去**：状态码的判定在 :meth:`AnthropicClient._request`
    里，于是注入的替身传输也享受同一套错误语义。
    """
    try:
        return requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderRequestError(f"provider 请求失败：{exc}") from exc
