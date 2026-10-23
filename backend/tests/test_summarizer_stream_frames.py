"""流式响应的收尾帧：只带 usage 的空帧不该把正文之后的流打断（工单 #65）。

背景：OpenAI 兼容的网关（本地聚合代理、自建推理服务）在正文之后常补一帧
**只带 usage 的帧**——``choices`` 是空数组。直接取 ``chunk.choices[0].delta``
会在那一帧上抛 IndexError，而异常被路由的通用分支接住，用户看到的是
「答案整段出来了，然后弹一句凭据无效或调用失败，请检查后重试」。

本文件断言的是**外部可观察行为**：正文产出什么、有没有抛异常。
不测私有函数的内部形状，也不断言源码里有某行字面量。
"""

from types import SimpleNamespace

import pytest

import summarizer


def _text_chunk(text):
    """一帧正常正文。"""
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _usage_chunk():
    """一帧只带 usage 的收尾帧：choices 是空数组（网关的真实行为）。"""
    return SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=42))


def _choice_without_delta():
    """一帧 choices 非空但没有 delta（另一种网关行为）。"""
    return SimpleNamespace(choices=[SimpleNamespace()])


class _FakeOpenAI:
    """替换 openai 客户端：不联网、不花钱，但如实按给定帧序列返回。"""

    frames: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        return list(_FakeOpenAI.frames)


@pytest.fixture()
def fake_openai(monkeypatch):
    _FakeOpenAI.frames = []
    monkeypatch.setattr(summarizer, "OpenAI", _FakeOpenAI)
    # 平台 key 也要换成假值：否则建客户端会把 backend/.env 里的真 key 带进测试进程。
    monkeypatch.setenv("ALIYUN_BAILIAN_API_KEY", "test-only-platform-key")
    return _FakeOpenAI


def _summarizer():
    return summarizer.VideoSummarizer()


def test_chat_stream_skips_usage_only_terminal_frame(fake_openai):
    """正文后面跟一帧只带 usage 的收尾帧：正文照常产出，不抛异常。"""
    fake_openai.frames = [_text_chunk("甲"), _usage_chunk()]

    assert list(_summarizer().chat_stream("字幕原文", "问题")) == ["甲"]


def test_chat_stream_keeps_text_around_an_empty_frame(fake_openai):
    """空帧夹在正文中间：空帧被跳过，两段正文都在。"""
    fake_openai.frames = [_text_chunk("甲"), _usage_chunk(), _text_chunk("乙")]

    assert list(_summarizer().chat_stream("字幕原文", "问题")) == ["甲", "乙"]


def test_chat_stream_tolerates_choice_without_delta(fake_openai):
    """choices 非空但没有 delta 的帧：跳过，不抛异常。"""
    fake_openai.frames = [_choice_without_delta(), _text_chunk("甲")]

    assert list(_summarizer().chat_stream("字幕原文", "问题")) == ["甲"]


def test_chat_stream_with_only_an_empty_frame_yields_nothing(fake_openai):
    """整条流只有空帧：没有正文，也没有异常。"""
    fake_openai.frames = [_usage_chunk()]

    assert list(_summarizer().chat_stream("字幕原文", "问题")) == []
