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

    def test_asr_api_key_traces_back_to_the_ops_env_only(self):
        """源码级钉死：ASR 真正交给 OpenAI 客户端的那把 key，只能来自 OPENAI_API_KEY。

        上一版只扫三个硬编码函数的源码里有没有别的 `*_API_KEY` 字面量，
        漏了一种写法：把取 key 塞进模块级 helper，`_transcribe_audio` 只调它。
        那样三个函数的源码里一个凭据名都没有，测试照绿，而用户的 key
        真的会流进 ASR。复审用这种写法实证过——用户 key 进了 OpenAI 客户端，
        两条测试全绿。

        所以这里不再看「出现了哪些名字」，而是**追数据来源**：
        锁定 `_transcribe_audio` 的子树（含它调用到的 helper），
        找出里面的 `OpenAI(api_key=...)`，把实参回溯到终点，
        要求终点是 `os.getenv("OPENAI_API_KEY")`。

        另有一条模块级断言，但那不是「不许出现第二个 key」——平台自己的
        LLM 后端本来就要读 `ALIYUN_BAILIAN_API_KEY` / `DEEPSEEK_API_KEY`。
        它守的是**工单 #9 的威胁模型**：全模块不得出现任何名字里带
        BYOK / USER / CLIENT 的凭据环境变量。
        """
        tree = ast.parse(inspect.getsource(summarizer))

        user_creds = {
            name for name in self._module_env_reads(tree)
            if any(tag in name.upper() for tag in ("BYOK", "USER", "CLIENT"))
        }
        assert not user_creds, (
            f"模块里读了疑似用户自带凭据的环境变量：{sorted(user_creds)}；"
            "工单 #9 引入 BYOK 时，这些也不该被 ASR 路径碰到"
        )

        asr_fn = next(
            (fn for fn in ast.walk(tree)
             if isinstance(fn, ast.FunctionDef) and fn.name == "_transcribe_audio"),
            None,
        )
        assert asr_fn is not None, "找不到 _transcribe_audio，这条测试等于没查"

        origins = []
        for call, expr in self._openai_api_key_args(asr_fn):
            origin = self._resolve(tree, expr)
            origins.append(origin)
            assert origin == ASR_KEY_ENV, (
                f"ASR 的 OpenAI(api_key=...) 实参回溯到 {origin!r} 而不是 "
                f"{ASR_KEY_ENV!r}；ASR 不许用任何别的凭据来源"
            )
        assert origins, "_transcribe_audio 里找不到 OpenAI(api_key=...) 调用"

    @staticmethod
    def _module_env_reads(tree):
        """模块里所有被读取的环境变量名（getenv / environ.get）。"""
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            func = node.func
            attr = getattr(func, "attr", "")
            is_environ_get = (
                attr == "get"
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
            )
            if attr == "getenv" or is_environ_get:
                names.add(first.value)
        return names

    @staticmethod
    def _openai_api_key_args(fn):
        """产出 (call 节点, api_key 实参表达式) —— 函数内每个 OpenAI(...) 调用一处。"""
        found = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(
                node.func, "attr", ""
            )
            if name != "OpenAI":
                continue
            for kw in node.keywords:
                if kw.arg == "api_key":
                    found.append((node, kw.value))
        return found

    @classmethod
    def _resolve(cls, tree, expr, seen=None):
        """把 api_key 实参回溯到它最终读取的环境变量名。

        只跟三种形态：直接读环境变量、局部变量赋值、helper 的 return。
        刻意不做完整数据流分析——目标不是形式化验证，是让「把用户 key
        接进 ASR」这种改法必须留下一个可查的 getenv 或一个可查的调用点；
        跟不上的形态会落到 None，由断言拦下。

        `seen` 挡住 `x = x` 这类自引用导致的无限递归。
        """
        seen = seen or set()

        # 形态一：直接读环境变量
        if isinstance(expr, ast.Call):
            name = cls._single_env_name(expr)
            if name:
                return name
            # 形态三：追进 helper，找它 return 的那个值
            return cls._resolve_helper_return(tree, expr, seen)

        # 形态二：局部变量，沿同模块内的赋值回溯
        if isinstance(expr, ast.Name):
            if expr.id in seen:
                return None
            seen.add(expr.id)
            for value in cls._assignments(tree, expr.id):
                origin = cls._resolve(tree, value, seen)
                if origin is not None:
                    return origin
            return None

        return None

    @staticmethod
    def _assignments(tree, var_name):
        """模块内所有 `var_name = ...` 的右值。"""
        out = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == var_name:
                        out.append(node.value)
        return out

    @staticmethod
    def _single_env_name(expr):
        func = expr.func
        attr = getattr(func, "attr", "")
        if attr in ("getenv", "get") and expr.args:
            first = expr.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
        return None

    @classmethod
    def _resolve_helper_return(cls, tree, call, seen):
        target = getattr(call.func, "id", None)
        if target is None:
            return None
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name != target:
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    origin = cls._resolve(tree, sub.value, seen)
                    if origin is not None:
                        return origin
        return None
        assert "database" not in "\n".join(sources), "ASR 不该去读用户表里的凭据"
