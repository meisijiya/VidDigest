"""ASR 音频下载的抖音分流（工单 #58）。

``summarizer`` 曾自带一份 ``_is_douyin_url``，名单还不一样。两份判定器必然漂移：
``douyin.is_douyin_url`` 收紧到域名边界之后，那份拷贝仍会把 ``notdouyin.com``
送去走抖音解析器——而它是这件事在 ASR 路径上**唯一**的出口。

所以这里断言的是出口上看得见的行为：**哪个下载器被构造了**。两头都装探针：
只装抖音侧的话，「走了 yt-dlp」与「哪边都没走」分不开。
"""
import pytest

import douyin as douyin_module
import downloader as downloader_module
import summarizer

#: 看着像抖音、其实不是的 host。子串匹配下它会被送去出网请求再失败。
NEAR_MISS = "https://notdouyin.com/x"


@pytest.fixture()
def asr_routing(monkeypatch, tmp_path):
    """两头都装记账探针，返回记账表。"""
    seen = {"douyin": [], "ytdlp": []}

    class _RecordingDouyinParser:
        def __init__(self, download_dir):
            seen["douyin"].append(download_dir)

        def download(self, url, mode="video"):
            # 下载发生在 try 内部，异常会被那条 except Exception 吞掉；
            # 而「被构造过」这件事已经记在账上了。
            raise RuntimeError("不该真的下载")

    class _RecordingYDL:
        def __init__(self, opts):
            seen["ytdlp"].append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            return {"id": "probe"}

    monkeypatch.setattr(downloader_module, "VideoDownloader",
                        lambda: type("D", (), {"DOWNLOAD_DIR": str(tmp_path)})(),
                        raising=True)
    monkeypatch.setattr(douyin_module, "DouyinParser", _RecordingDouyinParser,
                        raising=True)
    monkeypatch.setattr(summarizer.yt_dlp, "YoutubeDL", _RecordingYDL, raising=True)
    return seen


def test_a_near_miss_host_never_reaches_the_douyin_parser(asr_routing):
    """近似 host 走 yt-dlp 那支，抖音侧一次都不许被构造。"""
    assert summarizer._download_audio_for_asr(NEAR_MISS) is None

    assert asr_routing["douyin"] == [], "看着像抖音的 host 被送去抖音解析器了"
    assert asr_routing["ytdlp"], "反向对照：确实走了 yt-dlp 那一支"


@pytest.mark.parametrize("url", [
    "https://www.douyin.com/video/7300000000000000000",
    "https://v.douyin.com/abc123/",
    "https://www.iesdouyin.com/share/video/7300000000000000000",
])
def test_real_douyin_shapes_still_reach_the_douyin_parser(asr_routing, url):
    """收紧判定不能少掉任何一种真实分享链接——尤其 ``www.iesdouyin.com``。

    它是另一个注册域，删掉那条会让短链 302 到的终点在这里判成非抖音。
    """
    summarizer._download_audio_for_asr(url)

    assert len(asr_routing["douyin"]) == 1, f"{url} 没走抖音解析器"
    assert asr_routing["ytdlp"] == [], f"{url} 走错了支路"