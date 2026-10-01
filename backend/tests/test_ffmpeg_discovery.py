"""ffmpeg 探测（bug 5）：不在 import 阶段跑子进程，不含任何人的用户目录。"""
import os
import re
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(BACKEND)

# 任何形如 C:\Users\<某人>\ 的绝对路径都不该出现在源码里
HARD_CODED_USER_DIR = re.compile(r"[A-Za-z]:\\\\?Users\\\\?[A-Za-z0-9_.-]+", re.IGNORECASE)

SOURCE_EXT = (".py", ".js", ".vue", ".mjs")
SKIP_DIRS = {"venv", "node_modules", ".git", "dist", "__pycache__", "data",
             ".pytest_cache", ".workbuddy", "docs"}


def _iter_sources():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(SOURCE_EXT):
                yield os.path.join(root, name)


class TestNoHardCodedPaths:
    def test_no_absolute_user_profile_in_sources(self):
        """只扫源码。docs/ 与 .workbuddy/ 是笔记，可能合法记录某台机器的路径。"""
        hits = []
        for path in _iter_sources():
            with open(path, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if HARD_CODED_USER_DIR.search(line):
                        hits.append(f"{os.path.relpath(path, REPO)}:{i}")
        assert not hits, f"源码里出现硬编码用户目录: {hits}"


class TestNoImportTimeSideEffect:
    def test_import_summarizer_spawns_no_subprocess(self):
        """import 阶段跑 `ffmpeg -version` 会让任何脚本/测试都白白付探测代价。"""
        # 先把第三方依赖的 import 副作用付掉，测到的才是 summarizer 自己的行为。
        # 不能替换 subprocess.Popen —— yt_dlp 里有 `class Popen(subprocess.Popen)`。
        import yt_dlp  # noqa: F401
        import openai  # noqa: F401
        import httpx  # noqa: F401

        calls = []
        real_run = subprocess.run

        def recording(cmd, *a, **k):
            calls.append(cmd)
            return real_run(cmd, *a, **k)

        subprocess.run = recording
        try:
            for mod in list(sys.modules):
                if mod == "summarizer":
                    del sys.modules[mod]
            import summarizer  # noqa: F401
        finally:
            subprocess.run = real_run

        ffmpeg_calls = [c for c in calls if "ffmpeg" in str(c)]
        assert not ffmpeg_calls, f"import 阶段仍在探测 ffmpeg: {ffmpeg_calls}"

    def test_module_level_ffmpeg_constant_removed(self):
        import summarizer
        assert not hasattr(summarizer, "FFMPEG_PATH"), "应改为惰性 _ffmpeg_path()"


class TestLazyResolution:
    def test_resolver_is_cached(self):
        import summarizer
        first = summarizer._ffmpeg_path()
        assert first == summarizer._ffmpeg_path()

    def test_resolver_uses_no_subprocess(self):
        """解析本身也不该 fork —— shutil.which 已经在 PATH 上做完了。"""
        import summarizer
        summarizer._FFMPEG_PATH = None  # 强制重新解析
        calls = []
        real_run = subprocess.run

        def recording(cmd, *a, **k):
            calls.append(cmd)
            return real_run(cmd, *a, **k)

        subprocess.run = recording
        try:
            resolved = summarizer._ffmpeg_path()
        finally:
            subprocess.run = real_run
        assert not [c for c in calls if "ffmpeg" in str(c)], f"解析时仍 fork 了进程: {calls}"
        # 有 ffmpeg 时必须指向真实文件；没有时退回裸名交给调用方报错
        assert resolved == "ffmpeg" or os.path.isfile(resolved)

    def test_asr_uses_the_lazy_resolver(self):
        import inspect

        import summarizer
        assert "_ffmpeg_path()" in inspect.getsource(summarizer._extract_audio_for_asr)
