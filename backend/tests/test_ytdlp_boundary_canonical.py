"""取数边界先归一（工单 #30 第 1 件 · 域名别名）。

## 这组用例在守什么

`url_canonical.py` 早就把「B 站移动域名 = B 站长链」「小红书省略 www = 小红书
长链」写成规则了，但那些规则只写进数据库用于**比较**，从来没参与**取数**——
于是 yt-dlp 收到的是用户原样粘进来的那串，落进 `Generic` 兜底而不是平台
专用 extractor。

本片把归一接到 yt-dlp 边界上。判据是「**yt-dlp 实际收到的是哪一个字符串**」，
因为那才是第三方会不会接管这件事的真正分界。

## 为什么还要一组「逐字不变」的用例

`url_for_ytdlp` 对未识别的主机必须**逐字返回原文**。这不是锦上添花：
若哪天它开始 trim 一下空白或补个尾斜杠，绝大多数链接都会静默变形，
而症状是「某些链接莫名其妙解析不了」，极难回溯。

## 为什么最后一组在守数据库

ADR 0016 定的是「比较用规范值、回显用原文」。本次改动让「规范值」第一次
参与了运行时，如果有人顺手把归一挪到 `SummarizeRequest` 入口，
canonical 就会被写进 `video_url`，社区卡片回显的变成规范值——
**现有测试一条都不会红**，因为它们读的是同一个被改过的变量。
所以把「原文入库」单独立一条断言。
"""

import pytest

from downloader import VideoDownloader
import database
import summarizer
from summarizer import SubtitleExtractor

# 一条 B 站视频的三种形态。前两种 yt-dlp 的 BiliBili extractor **不认**。
BV = "BV1aa411c7mD"
BILI_MOBILE = f"https://m.bilibili.com/video/{BV}"
BILI_CANONICAL = f"https://www.bilibili.com/video/{BV}"

# 小红书笔记。`xsec_token` 是反爬参数，**必须原样带过去**。
XHS_ID = "6411cf99000000001300b6d9"
XHS_TOKEN = "CBgeL8Dxd1ZWBhwqRd568gAZ_iwG"
XHS_MOBILE_TOKEN = f"https://m.xiaohongshu.com/explore/{XHS_ID}?xsec_token={XHS_TOKEN}"
XHS_CANONICAL_TOKEN = f"https://www.xiaohongshu.com/explore/{XHS_ID}?xsec_token={XHS_TOKEN}"

# 一个本仓不认识的平台：归一对它必须是恒等变换。
UNKNOWN = "https://v.example/watch?v=abc&t=42"


@pytest.fixture()
def ytdlp_calls(monkeypatch):
    """把 yt-dlp 的网络出口换成记录器，返回它记下的 URL 列表。

    顺带钉住一个正向对照：桩自己必须真的接到了调用，
    否则「一个 URL 都没记到」会被后面几条断言读成「全都传对了」。
    """
    import yt_dlp

    calls: list[str] = []

    def extract_info(self, url, download=True, **kwargs):
        calls.append(url)
        return {
            "id": "stub",
            "title": "桩标题",
            "ext": "mp4",
            "url": "https://cdn.example/x.mp4",
            "formats": [],
        }

    monkeypatch.setattr(
        yt_dlp.YoutubeDL, "extract_info", extract_info, raising=True
    )
    return calls


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    """把下载目录指到 tmp_path，别在仓库里建 downloads/。"""
    monkeypatch.setattr(VideoDownloader, "DOWNLOAD_DIR", str(tmp_path / "dl"))
    return VideoDownloader()


# ── 三条 yt-dlp 取数出口 ────────────────────────────────

class TestYtdlpExitsReceiveCanonical:
    def test_parse_video_hands_canonical_to_ytdlp(self, downloader, ytdlp_calls):
        downloader.parse_video(BILI_MOBILE)
        assert ytdlp_calls == [BILI_CANONICAL]

    def test_download_video_hands_canonical_to_ytdlp(self, downloader, ytdlp_calls):
        downloader.download_video(BILI_MOBILE, "best")
        assert ytdlp_calls == [BILI_CANONICAL]

    def test_get_direct_url_hands_canonical_to_ytdlp(self, downloader, ytdlp_calls):
        downloader.get_direct_url(BILI_MOBILE, "best")
        assert ytdlp_calls == [BILI_CANONICAL]

    def test_one_form_stays_put(self, downloader, ytdlp_calls):
        """已经是规范形态的，喂给 yt-dlp 的必须还是它自己。"""
        downloader.parse_video(BILI_CANONICAL)
        assert ytdlp_calls == [BILI_CANONICAL]


# ── 字幕那条主功能路径 ──────────────────────────────────

class TestSubtitlePathReceivesCanonical:
    @pytest.fixture(autouse=True)
    def _stub_the_heavy_tail(self, monkeypatch):
        """extract() 在字幕拿不到后会落到 ASR。把那条尾巴也钉住，
        只留下我们要观察的那一段。"""
        seen: list[str] = []
        self.seen = seen

        def fake_info(url):
            seen.append(url)
            raise RuntimeError("桩：到此为止")

        def fake_asr(url):
            seen.append(url)
            return {
                "has_subtitle": False, "language": "", "subtitle_type": "none",
                "segments": [], "full_text": "",
                "fail_reason": "桩", "asr_fail_reason": "桩",
            }

        monkeypatch.setattr(SubtitleExtractor, "_get_video_info", staticmethod(fake_info))
        monkeypatch.setattr(summarizer, "_transcribe_audio", fake_asr)

    def test_xiaohongshu_keeps_its_anti_scraping_token(self):
        """小红书的 `xsec_token` 丢了就解析不了——这是它与 B 站最大的差别。

        若归一顺手把 query 抹掉，这里会红，而且症状在真机上就是
        「本来能解析的小红书链接突然解析不了」。
        """
        SubtitleExtractor().extract(XHS_MOBILE_TOKEN)
        assert self.seen == [XHS_CANONICAL_TOKEN, XHS_CANONICAL_TOKEN]

    def test_unknown_host_is_byte_identical(self):
        SubtitleExtractor().extract(UNKNOWN)
        assert self.seen == [UNKNOWN, UNKNOWN]


# ── 未识别的主机必须逐字不变 ────────────────────────────

class TestUnrecognizedHostIsUntouched:
    @pytest.mark.parametrize(
        "url",
        [
            UNKNOWN,
            # 短链：归一是本工单**明确排除**在外的部分，别顺手做了。
            "https://b23.tv/abcdefg",
            "https://xhslink.com/a/abcdefg",
            # 小红书主页不是笔记，别改写用户的地址。
            "https://www.xiaohongshu.com/user/profile/abc",
            # 爱奇艺的 .html 有实测理由保留（AGENTS.md），别当脏参数抹掉。
            "https://www.iqiyi.com/v_19rr7depsw.html",
            # 笔记路径后面还挂着东西：不是一条笔记，别把尾巴吃掉。
            # 这条守的是 `_XIAOHONGSHU_NOTE` 的 `$` 锚点——漏了它，
            # `/explore/<id>/extra` 会被改写成 `/explore/<id>`，
            # 于是两个不同的地址塌成同一个（且不报错）。
            f"https://m.xiaohongshu.com/explore/{XHS_ID}/extra",
        ],
    )
    def test_pass_through(self, downloader, ytdlp_calls, url):
        downloader.parse_video(url)
        assert ytdlp_calls == [url]


# ── 归一不得渗进数据库 ──────────────────────────────────

class TestOriginalTextIsStillWhatGetsStored:
    def test_reserve_video_keeps_the_users_own_text(self, db):
        """原文入库、规范值进 canonical_url——ADR 0016 的那条分界。

        这条与上一组是**互相牵制**的：只有「取数用规范值」与「存库用原文」
        同时成立，归一才既修了取数又不改写 #25 拍板的那个取舍。
        """
        db.reserve_video(BILI_MOBILE, 1)
        row = db.get_video_by_url(BILI_MOBILE)
        assert row["video_url"] == BILI_MOBILE
        assert row["canonical_url"] == BILI_CANONICAL

    def test_the_stored_row_is_still_reachable_by_its_original_form(self, db):
        """回显用原文，所以按原文必须查得到——否则卡片点不进去。"""
        db.reserve_video(XHS_MOBILE_TOKEN, 1)
        assert database.get_video_by_url(XHS_MOBILE_TOKEN) is not None