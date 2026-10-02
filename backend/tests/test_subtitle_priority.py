"""字幕优先级：人工 > 平台自动 > ASR（工单 #3）。

工单的假设是「自动字幕排在人工之前，本工单只调顺序」。读完代码后确认不是：
`_pick_best_subtitle` 先挑人工再挑自动，`extract` 的顺序是 B 站 API → yt-dlp
字幕 → ASR，优先级本来就对。真正缺的两样是：

1. **没有任何测试断言过 ASR 到底有没有被调用**——所以「有平台字幕就别烧语音识别」
   这条承诺从来没被守住过，将来谁把 ASR 提到字幕前面也不会有人发现。
2. **有字幕轨道却拿不到时会静默落穿**——一条明明带自动字幕的视频，字幕下载失败
   就悄悄去调语音识别，而且返回值和「这视频压根没字幕」长得一模一样。

所以这里每条测试都断言 ASR 的调用次数，并用 `fail_reason` 把两种落穿原因分开。
"""
import ast
import inspect
import logging

import pytest

import summarizer
from summarizer import SubtitleExtractor

#: ASR 侧唯一允许出现的凭据名。
ASR_KEY_ENV = "OPENAI_API_KEY"

#: BYOK 工单接入后，用户对话模型凭据大概会叫这些名字。
#: 下面两条测试就是拿它们当探针，验证 ASR 路径一个都不读。
USER_KEY_ENVS = (
    "BYOK_API_KEY",
    "USER_API_KEY",
    "VIDDIGEST_USER_API_KEY",
    "USER_OPENAI_API_KEY",
)

BILIBILI_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
OTHER_URL = "https://example.com/video/abc"

#: ASR 侧会被计数的入口。与 seams.MODEL_METHODS 同一形状。
ASR_METHODS = ("_transcribe_audio",)

SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": "第一句"},
    {"start": 1.5, "end": 3.0, "text": "第二句"},
]


def track(url: str) -> list:
    """一条字幕轨道（json3 格式）。"""
    return [{"ext": "json3", "url": url}]


def video_info(subtitles=None, auto_captions=None) -> dict:
    """yt-dlp 视频信息里本测试关心的两个字段。"""
    return {
        "subtitles": subtitles or {},
        "automatic_captions": auto_captions or {},
    }


def asr_unavailable() -> dict:
    """ASR 兜底没救回来时的返回形状，与 `_empty_asr(asr_fail_reason=...)` 一致。"""
    return {
        "has_subtitle": False,
        "language": "zh",
        "subtitle_type": "none",
        "segments": [],
        "full_text": "",
        "fail_reason": "",
        "asr_fail_reason": summarizer.FAIL_ASR_NOT_CONFIGURED,
    }


def asr_transcribed() -> dict:
    """ASR 兜底成功的返回形状。"""
    return {
        "has_subtitle": True,
        "language": "zh",
        "subtitle_type": "asr",
        "segments": SEGMENTS,
        "full_text": "转写结果",
        "fail_reason": "",
        "asr_fail_reason": "",
    }


class RecordingASR:
    """替换 `summarizer._transcribe_audio`，按方法计数。

    形状对齐 `seams.StubSummarizer`：字典计数 + `calls_of()`，
    未知方法名直接 KeyError——方法名拼错后拿到 0 会让断言假通过，
    而「ASR 调用次数为 0」恰恰是本文件每条测试的核心断言。
    """

    def __init__(self, result=None):
        self._calls = {m: 0 for m in ASR_METHODS}
        #: 每次调用返回它的拷贝，测试可以随时换结果
        self.result = asr_unavailable() if result is None else result

    def calls_of(self, method: str) -> int:
        if method not in self._calls:
            raise KeyError(f"未知的 ASR 方法 {method!r}；可计数的：{sorted(self._calls)}")
        return self._calls[method]

    @property
    def calls(self) -> int:
        return sum(self._calls.values())

    def __call__(self, url):
        self._calls["_transcribe_audio"] += 1
        return dict(self.result)


class Wiring:
    """把 `extract()` 的外部依赖全换成桩，留下三个观察口。

    `_pick_best_subtitle` 故意不换：人工优先于自动正是这里要守的行为，
    把它也换成桩就等于把被测的东西一起换掉了。
    """

    def __init__(self, monkeypatch, info=None, segments=None):
        self.info = video_info() if info is None else info
        self.segments = list(segments or [])
        self.info_calls = 0
        self.asr = RecordingASR()
        self.extractor = SubtitleExtractor()

        def _get_video_info(url):
            self.info_calls += 1
            return self.info

        def _segments(_):
            return list(self.segments)

        def _segments_via_ytdlp(_extractor, url, lang, sub_type):
            return list(self.segments)

        monkeypatch.setattr(SubtitleExtractor, "_get_video_info", staticmethod(_get_video_info))
        monkeypatch.setattr(SubtitleExtractor, "_download_subtitle_json", staticmethod(_segments))
        monkeypatch.setattr(
            SubtitleExtractor, "_download_and_parse_via_ytdlp", _segments_via_ytdlp
        )
        monkeypatch.setattr(summarizer, "_transcribe_audio", self.asr)

    def extract(self, url=OTHER_URL) -> dict:
        return self.extractor.extract(url)


@pytest.fixture()
def wiring(monkeypatch):
    return Wiring(monkeypatch)


# ── 场景一、二：拿到平台字幕时绝不该碰 ASR ────────────────

class TestPlatformSubtitleWins:
    def test_manual_subtitle_wins_and_asr_is_never_called(self, wiring):
        """有人工字幕就只用人工，ASR 调用次数必须是 0。"""
        wiring.info = video_info(
            subtitles={"zh-Hans": track("https://cdn/manual.json3")},
            auto_captions={"en": track("https://cdn/auto-en.json3")},
        )
        wiring.segments = SEGMENTS

        result = wiring.extract()

        assert result["has_subtitle"] is True
        # 人工与自动都在时必须选人工：反序实现会让这里变成 auto
        assert result["subtitle_type"] == "manual", "人工字幕必须排在平台自动字幕前面"
        assert result["language"] == "zh-Hans"
        assert result["full_text"] == "第一句第二句"
        assert result["fail_reason"] == ""
        assert result["asr_fail_reason"] == ""
        assert wiring.asr.calls_of("_transcribe_audio") == 0

    def test_auto_subtitle_used_when_no_manual_and_asr_never_called(self, wiring):
        """只有平台自动字幕时用它，ASR 调用次数仍然是 0。"""
        wiring.info = video_info(auto_captions={"zh": track("https://cdn/auto-zh.json3")})
        wiring.segments = SEGMENTS

        result = wiring.extract()

        assert result["has_subtitle"] is True
        assert result["subtitle_type"] == "auto"
        assert result["full_text"] == "第一句第二句"
        assert wiring.asr.calls_of("_transcribe_audio") == 0

    def test_bilibili_api_hit_skips_ytdlp_and_asr(self, wiring, monkeypatch):
        """B 站专用 API 命中就不该再问 yt-dlp，更不该调 ASR。"""
        monkeypatch.setattr(
            SubtitleExtractor,
            "_extract_bilibili",
            lambda self, url: {
                "has_subtitle": True,
                "language": "zh-Hans",
                "subtitle_type": "manual",
                "segments": SEGMENTS,
                "full_text": "B 站字幕",
                "fail_reason": "",
                "asr_fail_reason": "",
            },
        )

        result = wiring.extract(BILIBILI_URL)

        assert result["has_subtitle"] is True
        assert result["full_text"] == "B 站字幕"
        assert wiring.asr.calls_of("_transcribe_audio") == 0
        assert wiring.info_calls == 0, "B 站命中后不该再去查 yt-dlp 的字幕轨道"


# ── 场景三、四：落穿到 ASR 必须显式、可区分 ─────────────────

class TestFallthroughIsExplicit:
    def test_track_present_but_fetch_empty_falls_back_once(self, wiring):
        """有字幕轨道但一条都没解析出来：仍然落穿 ASR，但原因要说清楚。"""
        wiring.info = video_info(subtitles={"zh-Hans": track("https://cdn/manual.json3")})
        wiring.segments = []  # 下载没报错，但内容是空的

        result = wiring.extract()

        assert result["has_subtitle"] is False
        assert result["fail_reason"] == summarizer.FAIL_FETCH_FAILED
        assert result["asr_fail_reason"] == summarizer.FAIL_ASR_NOT_CONFIGURED
        assert wiring.asr.calls_of("_transcribe_audio") == 1

    def test_fallthrough_reason_is_logged(self, wiring, caplog):
        """落穿不是静默的：日志里要能看出这次是因为什么才调的 ASR。"""
        wiring.info = video_info(auto_captions={"zh": track("https://cdn/auto-zh.json3")})
        wiring.segments = []

        with caplog.at_level(logging.INFO, logger="summarizer"):
            wiring.extract()

        assert summarizer.FAIL_FETCH_FAILED in caplog.text, (
            f"落穿原因没有进日志，用户无从知道为什么烧了 ASR：{caplog.text}"
        )

    def test_no_track_at_all_falls_back_once(self, wiring):
        """平台压根没给字幕轨道：落穿 ASR 一次，原因与上一条不同。"""
        wiring.info = video_info()

        result = wiring.extract()

        assert result["has_subtitle"] is False
        assert result["fail_reason"] == summarizer.FAIL_NO_TRACK
        assert wiring.asr.calls_of("_transcribe_audio") == 1

    def test_successful_asr_fallback_carries_no_failure_reason(self, wiring):
        """ASR 兜底成功就是成功：不得带着平台侧的失败原因返回。"""
        wiring.info = video_info()
        wiring.asr.result = asr_transcribed()

        result = wiring.extract()

        assert result["has_subtitle"] is True
        assert result["subtitle_type"] == "asr"
        assert result["fail_reason"] == "", "ASR 成功时不该再报平台字幕失败"
        assert result["asr_fail_reason"] == ""
        assert wiring.asr.calls_of("_transcribe_audio") == 1

    def test_missing_track_and_failed_fetch_are_not_the_same_outcome(self, wiring):
        """「本来就没有」和「本来有却没拿到」必须在返回值里分得开。"""
        wiring.info = video_info(subtitles={"zh-Hans": track("https://cdn/manual.json3")})
        wiring.segments = []
        fetch_failed = wiring.extract()
        after_first = wiring.asr.calls_of("_transcribe_audio")

        wiring.info = video_info()  # 平台没给任何轨道
        no_track = wiring.extract()

        assert fetch_failed["fail_reason"] == summarizer.FAIL_FETCH_FAILED
        assert no_track["fail_reason"] == summarizer.FAIL_NO_TRACK
        assert fetch_failed["fail_reason"] != no_track["fail_reason"]
        # 两次都落穿了，但每次只烧一次 ASR
        assert wiring.asr.calls_of("_transcribe_audio") == after_first + 1 == 2

    def test_asr_without_key_reports_not_configured(self, monkeypatch):
        """真跑一次没有 key 的 ASR：原因要写出来，且不该再去下载音频。"""
        monkeypatch.delenv(ASR_KEY_ENV, raising=False)
        monkeypatch.setattr(
            summarizer,
            "_download_audio_for_asr",
            lambda url: pytest.fail("没有 OPENAI_API_KEY 时不该去下载音频"),
        )

        result = summarizer._transcribe_audio(OTHER_URL)

        assert result["has_subtitle"] is False
        assert result["asr_fail_reason"] == summarizer.FAIL_ASR_NOT_CONFIGURED


# ── 场景五（工单 AC 4）：ASR 不吃用户的对话模型凭据 ─────────

class TestAsrCredentialsAreOpsOnly:
    def test_asr_uses_its_own_key_even_when_user_keys_exist(self, tmp_path, monkeypatch):
        """环境里摆满用户凭据，ASR 拿到的仍必须是运维那把 key。"""
        audio = tmp_path / "asr.mp3"
        audio.write_bytes(b"fake-audio")
        monkeypatch.setenv(ASR_KEY_ENV, "sk-ops-key")
        for name in USER_KEY_ENVS:
            monkeypatch.setenv(name, f"user-key-in-{name}")

        used = {}

        class _FakeTranscriptions:
            def create(self, **kwargs):
                class _Transcript:
                    text = "转写结果"
                    segments = []
                return _Transcript()

        class _FakeClient:
            def __init__(self, api_key):
                used["api_key"] = api_key
                self.audio = type("Audio", (), {"transcriptions": _FakeTranscriptions()})()

        monkeypatch.setattr(summarizer, "_download_audio_for_asr", lambda url: str(audio))
        monkeypatch.setattr(summarizer, "OpenAI", _FakeClient)

        result = summarizer._transcribe_audio(OTHER_URL)

        assert result["has_subtitle"] is True
        assert used["api_key"] == "sk-ops-key", f"ASR 用错了凭据来源：{used['api_key']!r}"

    def test_asr_path_names_no_other_credential(self):
        """源码级钉死：ASR 这条路上只许出现 OPENAI_API_KEY 一个凭据名。

        不去管它用什么方式读（getenv / environ.get / 下标），只看源码里出现的
        凭据名——这样「后来有人把用户 key 接进 ASR」无论写成哪种形态都会红。
        """
        sources = [
            inspect.getsource(fn)
            for fn in (
                summarizer._transcribe_audio,
                summarizer._download_audio_for_asr,
                summarizer._extract_audio_for_asr,
            )
        ]

        keys = set()
        for src in sources:
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and node.value.endswith("_API_KEY"):
                    keys.add(node.value)

        assert keys == {ASR_KEY_ENV}, f"ASR 路径里出现了别的凭据名：{sorted(keys)}"
        assert "database" not in "\n".join(sources), "ASR 不该去读用户表里的凭据"
