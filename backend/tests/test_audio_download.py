"""纯音频下载：B站/DASH 源与抖音两条路都要真的给得出音频选项。

这个文件是 `downloader.py` 的**首个**测试文件。它之前零覆盖，于是「只下音频」
这条能力既没有实现、也没有任何东西会发现它没实现。

删除语义（改这段判据前先读一遍）：

* DASH 源（B站 / YouTube）本来只给分离的视频流和音频流，没有「带声视频」这一项。
  界面上的画质列表曾经一条音频都没有 —— 不是选项被藏了，是 `_extract_formats`
  里一句 ``if not has_video: continue`` 把它们整批丢掉了。
* 判据全部落在**用户真正读的那个出口**上：``POST /api/parse`` 的 JSON 与
  ``POST /api/download`` 真的下下来的那个文件名。不测 ``_extract_formats`` /
  ``_build_result`` 这些私有函数 —— 它们是实现，改名换写法都不该让这些断言变红。
* 每条断言都问过一句「功能整体坏掉时它还会绿吗」。例如音频项的 ``format_id``
  必须真的能原样回传给下载接口：一个点得下去、下回来却还是视频的选项，
  对用户来说跟没有这个选项没有区别。
"""
import pytest
import yt_dlp

import main as main_module
from douyin import AUDIO_FORMAT_ID, VIDEO_FORMAT_ID, DouyinParser
from seams import make_client

BILIBILI = "https://www.bilibili.com/video/BV163aC6iEc5"
DOUYIN = "https://www.douyin.com/video/7300000000000000000"


# ── 网络边界桩 ────────────────────────────────────────────

def _stub_ytdl(monkeypatch, info):
    """把 yt-dlp 的网络出口换成固定 info。parse_video 里唯一的外来依赖。"""
    def extract_info(self, url, download=True, **kwargs):
        return info

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract_info,
                        raising=True)


def _stub_douyin(monkeypatch, item, download_dir):
    """把抖音的三处网络出口换成桩，download_dir 指向 tmp_path。

    ``_download_file`` 也要桩：它真下载。主流程里
    ``os.path.exists(filepath)`` 会因此看到真文件，断言的不是想象中的产物。
    """
    parser = main_module.douyin_parser
    monkeypatch.setattr(DouyinParser, "_resolve_redirect",
                        lambda self, url: url, raising=True)
    monkeypatch.setattr(DouyinParser, "_fetch_item_info",
                        lambda self, video_id, resolved_url: item, raising=True)

    def fake_download_file(self, url, filepath):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(b"fake")

    monkeypatch.setattr(DouyinParser, "_download_file", fake_download_file,
                        raising=True)
    monkeypatch.setattr(parser, "download_dir", download_dir, raising=True)
    return parser


# ── 造数据 ────────────────────────────────────────────────

def _info(formats):
    return {
        "id": "BV163aC6iEc5",
        "title": "测试标题",
        "duration": 615,
        "uploader": "叶雨kkksk",
        "extractor": "bilibili",
        "formats": formats,
    }


def _video_stream(h, ext="mp4"):
    return {
        "format_id": f"video-{h}",
        "ext": ext,
        "vcodec": "avc1.640028",
        "acodec": "none",
        "width": int(h * 16 / 9),
        "height": h,
        "filesize": h * 1_000_000,
    }


def _audio_stream(fid, abr, ext="m4a", acodec="mp4a.40.2", size=3_600_000):
    return {
        "format_id": fid,
        "ext": ext,
        "vcodec": "none",
        "acodec": acodec,
        "abr": abr,
        "filesize": size,
    }


def _douyin_item(with_music=True):
    item = {
        "desc": "测试标题",
        "author": {"nickname": "测试作者"},
        "statistics": {"play_count": 1234},
        "video": {
            "width": 1080,
            "height": 1920,
            "duration": 15000,
            "play_addr": {"url_list": ["https://example.com/playwm/a.mp4"]},
            "cover": {"url_list": ["https://example.com/cover.jpg"]},
        },
    }
    if with_music:
        item["music"] = {
            "play_url": {"url_list": ["https://example.com/music.mp3"]},
        }
    return item


@pytest.fixture()
def client():
    # 不 `with client`：lifespan 会跑 init_db() 播种，与本文件无关。
    return make_client(main_module.app)


def _parse(client, url):
    resp = client.post("/api/parse", json={"url": url})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _kinds(formats):
    return [f["kind"] for f in formats]


# ── DASH 源：音频必须进得来 ────────────────────────────────

class TestDashSourceOffersAudio:
    """B站这类只给分离流的源。用户要的是「只下音频」，不是「低码率视频」。"""

    def test_a_pure_audio_option_is_offered(self, client, monkeypatch):
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _video_stream(720), _video_stream(360),
            _audio_stream("audio-302", 302),
        ]))
        formats = _parse(client, BILIBILI)["formats"]
        assert "audio" in _kinds(formats)

    def test_audio_option_is_audio_only_and_says_so(self, client, monkeypatch):
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _audio_stream("audio-302", 302),
        ]))
        audio = [f for f in _parse(client, BILIBILI)["formats"]
                 if f["kind"] == "audio"]
        assert audio, "没有音频选项"
        for opt in audio:
            assert opt["has_video"] is False
            assert opt["has_audio"] is True
            # 音频没有分辨率。写一个 1080 之类的值就是在撒谎。
            assert opt["resolution"] == ""

    def test_audio_option_carries_a_requestable_format_id(self, client,
                                                          monkeypatch):
        """format_id 会被前端原样回传给 /api/download。

        断言 ``format_id`` 非空还不够 —— 它必须真的是音频流自己的 id，
        而不是复制了某个视频流或合并项的 id，否则用户选音频会下到视频。
        """
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _audio_stream("audio-302", 302),
        ]))
        formats = _parse(client, BILIBILI)["formats"]
        audio_ids = [f["format_id"] for f in formats if f["kind"] == "audio"]
        video_ids = [f["format_id"] for f in formats if f["kind"] == "video"]
        assert audio_ids, "音频选项没有可回传的 format_id"
        for aid in audio_ids:
            assert aid and aid not in video_ids

    def test_every_option_declares_its_kind(self, client, monkeypatch):
        """每一条都要自报 kind。

        「缺字段就当视频」正是当初让这个问题看不见的那个默认：前端按 kind
        分组，一旦某条漏了字段，它会悄悄归进视频区，用户永远看不到。
        """
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _video_stream(720),
            _audio_stream("audio-302", 302),
            # 一条带声的老式流：既是视频也带声，仍必须自报 kind
            dict(_video_stream(480), acodec="mp4a.40.2"),
        ]))
        formats = _parse(client, BILIBILI)["formats"]
        assert formats
        for opt in formats:
            assert opt.get("kind") in ("video", "audio"), opt

    def test_video_options_are_not_mislabelled(self, client, monkeypatch):
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _video_stream(720),
            _audio_stream("audio-302", 302),
        ]))
        for opt in _parse(client, BILIBILI)["formats"]:
            if opt["kind"] == "video":
                assert opt["has_video"] is True

    def test_streams_with_neither_track_are_dropped(self, client, monkeypatch):
        """预览图 / 字幕轨不是可下载内容，别把上限浪费在它们身上。"""
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080),
            _audio_stream("audio-302", 302),
            {"format_id": "storyboard", "ext": "mhtml",
             "vcodec": "none", "acodec": "none"},
        ]))
        ids = [f["format_id"] for f in _parse(client, BILIBILI)["formats"]]
        assert "storyboard" not in ids

    def test_near_duplicate_audio_bitrates_collapse(self, client, monkeypatch):
        """DASH 源常给四五个彼此只差几十 k 的 opus 码率。

        全列出来等于给用户一堆没有实际差别的选择，还把真正要选的画质挤出上限。
        """
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080),
            _audio_stream("a-301", 301),
            _audio_stream("a-302", 302),
            _audio_stream("a-302b", 302),
            _audio_stream("a-64", 64),
        ]))
        audios = [f for f in _parse(client, BILIBILI)["formats"]
                  if f["kind"] == "audio"]
        abrs = [f["abr"] for f in audios]
        assert len(abrs) == len(set(abrs)), f"码率重复未被合并: {abrs}"

    def test_audio_options_come_out_highest_bitrate_first(self, client,
                                                          monkeypatch):
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080),
            _audio_stream("a-64", 64),
            _audio_stream("a-320", 320),
            _audio_stream("a-128", 128),
        ]))
        audios = [f for f in _parse(client, BILIBILI)["formats"]
                  if f["kind"] == "audio"]
        assert [f["abr"] for f in audios] == [320, 128, 64]

    def test_audio_with_unknown_bitrate_sinks_to_the_bottom(self, client,
                                                            monkeypatch):
        """码率缺失不是「最优先」，不是「随机」—— 沉到已知码率后面。"""
        unknown = _audio_stream("a-unknown", None)
        unknown.pop("abr")
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), unknown, _audio_stream("a-128", 128),
        ]))
        audios = [f for f in _parse(client, BILIBILI)["formats"]
                  if f["kind"] == "audio"]
        assert audios[-1]["format_id"] == "a-unknown"

    def test_audio_survives_a_full_screen_of_video_options(self, client,
                                                           monkeypatch):
        """上限 15，但**先给音频留位置**。

        这是「用户看不到音频选项」的第二种成因：视频先把格子占满，
        音频接在后面就被静默截断。

        视频流的高度全部取不同值：去重的键是 (height, ext)，高度撞了
        就会被折掉。折掉之后上限根本碰不到，这条用例就在一个
        不可能失败的状态上绿着 —— 它曾经就是这么绿的，
        而对应的实现是坏的（变异 M4 SURVIVED）。
        """
        videos = [_video_stream(1000 + i) for i in range(20)]
        _stub_ytdl(monkeypatch, _info(
            videos + [_audio_stream("a-128", 128), _audio_stream("a-64", 64)]
        ))
        formats = _parse(client, BILIBILI)["formats"]
        assert len(formats) <= main_module.downloader.MAX_FORMATS

        # 上限真的被碰到了吗？没碰到的话，下面那条断言证明不了任何东西。
        offered_videos = [f for f in formats if f["kind"] == "video"]
        assert len(offered_videos) + 2 == main_module.downloader.MAX_FORMATS, (
            f"上限没有被碰到（视频只剩 {len(offered_videos)} 条），"
            "这条用例测不到「音频被挤掉」这件事"
        )
        assert "audio" in _kinds(formats), (
            f"{len(videos)} 条视频把音频挤没了 —— "
            "上限截断发生在给音频留位之前"
        )

    def test_merged_option_leads_and_is_still_a_video_option(self, client,
                                                            monkeypatch):
        """全部分离流时合并项照旧排第一 —— 那是这个修复不该碰掉的行为。"""
        _stub_ytdl(monkeypatch, _info([
            _video_stream(1080), _video_stream(720),
            _audio_stream("a-128", 128),
        ]))
        formats = _parse(client, BILIBILI)["formats"]
        top = formats[0]
        assert "+" in top["format_id"], top
        assert top["has_audio"] is True
        assert top["kind"] == "video"


# ── 抖音：音频选项与 main.py 的路由 ───────────────────────

class TestDouyinOffersAudio:
    """抖音这边能力早就写好了（``_get_media_url(mode="audio")`` 出 .mp3），
    只是从没有人调用它，也没在解析结果里公布过 —— 能力写好了却不可达。"""

    def test_a_pure_audio_option_is_offered(self, client, monkeypatch,
                                            tmp_path):
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        formats = _parse(client, DOUYIN)["formats"]
        assert AUDIO_FORMAT_ID in [f["format_id"] for f in formats]

    def test_audio_option_is_marked_audio_only(self, client, monkeypatch,
                                               tmp_path):
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        formats = _parse(client, DOUYIN)["formats"]
        audio = [f for f in formats if f["format_id"] == AUDIO_FORMAT_ID]
        assert audio
        assert audio[0]["kind"] == "audio"
        assert audio[0]["has_video"] is False
        assert audio[0]["ext"] == "mp3"

    def test_video_option_is_marked_video(self, client, monkeypatch, tmp_path):
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        formats = _parse(client, DOUYIN)["formats"]
        video = [f for f in formats if f["format_id"] == VIDEO_FORMAT_ID]
        assert video, "视频选项不见了"
        assert video[0]["kind"] == "video"
        assert video[0]["has_video"] is True

    def test_a_source_without_music_offers_no_audio_option(self, client,
                                                           monkeypatch,
                                                           tmp_path):
        """没有音乐信息的源不给音频选项 —— 给一个必然 404 的选项是骗人。"""
        _stub_douyin(monkeypatch, _douyin_item(with_music=False), tmp_path)
        formats = _parse(client, DOUYIN)["formats"]
        assert AUDIO_FORMAT_ID not in [f["format_id"] for f in formats]
        assert VIDEO_FORMAT_ID in [f["format_id"] for f in formats]


class TestDouyinDownloadRouting:
    """抖音那条下载路自己决定下什么，只认 mode 不认 format_id。

    不映射的后果是**静默**的：界面上写着「下载音频」，用户拿到的是一个带声的
    mp4，而且不报错。所以这里的判据不是「有没有传 mode」，是**下回来的那个
    文件到底是什么**。
    """

    @staticmethod
    def _served_name(resp):
        assert resp.status_code == 200, resp.text
        return resp.headers["content-disposition"]

    def test_choosing_audio_actually_downloads_audio(self, client, monkeypatch,
                                                     tmp_path):
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        resp = client.post("/api/download",
                           json={"url": DOUYIN, "format_id": AUDIO_FORMAT_ID})
        assert ".mp3" in self._served_name(resp), \
            f"选了纯音频，下回来的不是 mp3: {self._served_name(resp)}"

    def test_choosing_video_still_downloads_video(self, client, monkeypatch,
                                                  tmp_path):
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        resp = client.post("/api/download",
                           json={"url": DOUYIN, "format_id": VIDEO_FORMAT_ID})
        assert ".mp4" in self._served_name(resp)

    def test_the_default_format_id_does_not_switch_to_audio(self, client,
                                                            monkeypatch,
                                                            tmp_path):
        """``format_id`` 缺省时必须仍是视频，不能因为"有音频了"就改成音频。"""
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        resp = client.post("/api/download", json={"url": DOUYIN})
        assert ".mp4" in self._served_name(resp)

    def test_an_unrecognised_format_id_falls_back_to_video(self, client,
                                                           monkeypatch,
                                                           tmp_path):
        """抖音不认 format_id，任何非音频 id 都落回视频，而不是报错或下空。"""
        _stub_douyin(monkeypatch, _douyin_item(), tmp_path)
        resp = client.post("/api/download",
                           json={"url": DOUYIN, "format_id": "137+140"})
        assert ".mp4" in self._served_name(resp)
