"""下载与解析两条路由的**错误路径**，以及它们的非抖音分支（工单 #53）。

三处此前完全没有覆盖：

1. ``download_video`` 里的 ``except HTTPException: raise``（``main.py:164-165``）。
   它排在 ``except Exception → 400`` **之前**，作用是让
   ``raise HTTPException(500, \"下载的文件不存在\")`` 原样透出。
   删掉那两行，全量测试不会有一条变红——本文件第 1 组用例就是为关掉这个缺口。
2. ``/api/download`` 的**非抖音分支**。全仓原有的 4 处该路由调用全是抖音域名，
   ``main.py:152-154`` 调 ``downloader.download_video`` 那条路没有任何路由层用例。
3. ``/api/parse`` 的 ``except Exception → 400`` 错误映射。
   ``test_audio_download.py`` 的 ``_parse()`` 助手自带 ``assert status_code == 200``，
   把这条路径永久挡在测试之外。

桩的落点：全部落在**网络边界与产物存在性**这两层，不桩 ``download_video`` 本身。
桩掉被测函数等于把要量的东西一起换掉了——``test_subtitle_priority.py`` 的注释
已经写过这条。
"""
import pytest

import main as main_module
import douyin as douyin_module
from seams import make_client

BILIBILI = "https://www.bilibili.com/video/BV163aC6iEc5"
DOUYIN = "https://www.douyin.com/video/7300000000000000000"
MISSING = "这个路径上的文件不会存在.mp4"


@pytest.fixture()
def client_app(db):
    """真实 app + 真实 client。刻意不 ``with client``（会跑 lifespan 重置 db 夹具）。"""
    return make_client(main_module.app)


@pytest.fixture()
def calls(monkeypatch):
    """按方法分别计数的桩记录表。

    不用「对侧没被 stub 所以会红」那种隐含判别力——那取决于网络，
    在有网的机器上会变成慢红或假绿。

    两个桩都返回一个**不存在的**产物路径，于是路由必然落到 500 分支。
    分流用例只关心「走了哪一边」，产物存在与否由上面那组用例单独量。
    """
    recorder = {"douyin": [], "ytdlp": []}

    def fake_douyin_download(url, mode):
        recorder["douyin"].append((url, mode))
        return {"filepath": MISSING, "filename": "unused.mp4"}

    def fake_ytdlp_download(url, format_id):
        recorder["ytdlp"].append((url, format_id))
        return {"filepath": MISSING, "filename": "unused.mp4"}

    monkeypatch.setattr(
        main_module.douyin_parser, "download", fake_douyin_download, raising=True
    )
    monkeypatch.setattr(
        main_module.downloader, "download_video", fake_ytdlp_download, raising=True
    )
    return recorder


# ── 1. except HTTPException: raise ─────────────────────────────


class TestMissingArtifactIsNotSwallowed:
    """产物不存在时必须拿到 **500 + 那句文案**，不是 400。

    为什么这一组值钱：它只在那两行 ``except HTTPException: raise``
    被删掉时转红。原有的 4 条下载用例永远走不到这个分支——它们让桩真写文件，
    ``os.path.exists`` 恒为真。
    """

    @pytest.mark.parametrize("url", [DOUYIN, BILIBILI])
    def test_returns_500_with_its_own_message(self, client_app, monkeypatch, url):
        """两条分支都要：抖音与 yt-dlp 各走一遍，两边的 except 都盖住。"""
        monkeypatch.setattr(
            main_module.douyin_parser, "download",
            lambda u, mode: {"filepath": MISSING, "filename": "x.mp4"},
            raising=True,
        )
        monkeypatch.setattr(
            main_module.downloader, "download_video",
            lambda u, f: {"filepath": MISSING, "filename": "x.mp4"},
            raising=True,
        )
        r = client_app.post("/api/download", json={"url": url})
        assert r.status_code == 500
        assert r.json()["detail"] == "下载的文件不存在"

    def test_swallowed_would_have_looked_like_a_400_with_a_wrapped_error(
        self, client_app, monkeypatch
    ):
        """反向对照：普通异常**确实**走 400，且 detail 是那个嵌套信封。

        这一条同时钉住 except 的**次序**：500 那条与 400 这条同时成立，
        才说明 ``except HTTPException: raise`` 在前、``except Exception`` 在后。
        只钉其中一条的话，把两段顺序对调也未必红。
        """
        monkeypatch.setattr(
            main_module.douyin_parser, "download",
            lambda u, mode: (_ for _ in ()).throw(RuntimeError("上游炸了")),
            raising=True,
        )
        monkeypatch.setattr(
            main_module.downloader, "download_video",
            lambda u, f: (_ for _ in ()).throw(RuntimeError("上游炸了")),
            raising=True,
        )
        r = client_app.post("/api/download", json={"url": DOUYIN})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["success"] is False
        assert "上游炸了" in detail["error"]

    def test_existing_artifact_is_served(self, client_app, monkeypatch, tmp_path):
        """反向对照：文件真的存在时不该落到任何一个 except。"""
        real = tmp_path / "ok.mp4"
        real.write_bytes(b"real-bytes")
        monkeypatch.setattr(
            main_module.douyin_parser, "download",
            lambda u, mode: {"filepath": str(real), "filename": "ok.mp4"},
            raising=True,
        )
        r = client_app.post("/api/download", json={"url": DOUYIN})
        assert r.status_code == 200
        assert r.content == b"real-bytes"


# ── 2. /api/download 的非抖音分支 ─────────────────────────────


class TestNonDouyinBranch:
    def test_bilibili_goes_to_yt_dlp_and_not_to_douyin(self, client_app, calls):
        """B 站 URL 走 ``downloader.download_video``，抖音侧**一次都不该被调**。

        两个方向都要断：只断「抖音被调了」的话，一个两边都调的实现照样绿。
        """
        client_app.post("/api/download", json={"url": BILIBILI})
        assert calls["ytdlp"] == [(BILIBILI, "bestvideo+bestaudio/best")]
        assert calls["douyin"] == []

    def test_douyin_goes_to_the_douyin_parser(self, client_app, calls):
        """反向对照：抖音 URL 必须走抖音侧，``format_id`` 不下传。"""
        client_app.post("/api/download", json={"url": DOUYIN})
        assert calls["douyin"] == [(DOUYIN, "video")]
        assert calls["ytdlp"] == []

    def test_format_id_is_forwarded_verbatim_on_the_yt_dlp_branch(
        self, client_app, monkeypatch, calls
    ):
        """选了哪个画质，下游拿到的就是哪个——不是恒为默认值。"""
        client_app.post("/api/download", json={"url": BILIBILI, "format_id": "137+140"})
        assert calls["ytdlp"] == [(BILIBILI, "137+140")]


# ── 3. /api/parse 的错误映射 ──────────────────────────────────


class TestParseErrorMapping:
    def test_downstream_failure_becomes_400_with_the_reason(self, client_app, monkeypatch):
        """下游抛普通异常 → 400，且 reason 原文进 ``error``。

        逐字断言而不是「包含」：文案是接口的一部分，前端
        ``lib/errors.js`` 靠它分类。
        """
        monkeypatch.setattr(
            main_module.downloader, "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("Unsupported URL")),
            raising=True,
        )
        r = client_app.post("/api/parse", json={"url": BILIBILI})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail == {"success": False, "error": "解析失败: Unsupported URL"}

    def test_successful_parse_is_not_wrapped_in_the_error_envelope(
        self, client_app, monkeypatch
    ):
        """反向对照：成功时返回 ``{success, data}``，不是错误信封。"""
        monkeypatch.setattr(
            main_module.downloader, "parse_video",
            lambda url: {"id": "BV1", "formats": []}, raising=True,
        )
        r = client_app.post("/api/parse", json={"url": BILIBILI})
        assert r.status_code == 200
        assert r.json() == {"success": True, "data": {"id": "BV1", "formats": []}}


# ── 4. /api/parse 的分流（工单 #56）─────────────────────────────
#
# 与上面第 2 节同源：分流此前只有「对侧没被 stub 所以会红」这种隐含判别力，
# 没有任何用例断言「走了哪一边」。这里按方法分别计数，两个方向都断。


@pytest.fixture()
def parse_calls(monkeypatch):
    """``/api/parse`` 两条支路的计数桩。

    ``douyin_parser`` 是实例——挂在实例属性上的普通函数**不会被传 self**；
    ``downloader`` 是模块，函数就是函数。形状不同，写反了报的是
    ``missing 1 required positional argument``，看着像参数写错。
    """
    recorder = {"douyin": [], "ytdlp": []}

    monkeypatch.setattr(
        main_module.douyin_parser, "parse",
        lambda url: (recorder["douyin"].append(url) or {"id": "dy"}), raising=True,
    )
    monkeypatch.setattr(
        main_module.downloader, "parse_video",
        lambda url: (recorder["ytdlp"].append(url) or {"id": "bv"}), raising=True,
    )
    return recorder


class TestDouyinMatcher:
    """``is_douyin_url`` 本体——分流用例够不着的那一层。

    为什么必须单独测：分流用例只能证明「这些 URL 走了抖音侧」。
    而 ``is_douyin_url`` 是**子串匹配**（``any(d in host for d in douyin_domains)``），
    于是名单里 ``iesdouyin.com`` / ``v.douyin.com`` / ``www.douyin.com`` /
    ``m.douyin.com`` **四条全是冗余的**——它们的 host 都包含 ``douyin.com``。
    变异实测：单独删掉其中任何一条，全量照样绿。

    所以「逐个钉住名单里的每一条」在行为层**做不到**，那是这份实现的性质，
    不是断言的漏洞。能钉住的是「整张名单空了必须变 False」，
    以及下面那些形态各自的判定结果。
    """

    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/video/7300000000000000000",
        "https://douyin.com/video/7300000000000000000",
        "https://v.douyin.com/abc123/",
        "https://www.iesdouyin.com/share/video/7300000000000000000",
        "https://m.douyin.com/share/video/7300000000000000000",
    ])
    def test_recognises_every_douyin_form(self, url):
        assert douyin_module.is_douyin_url(url) is True, url

    @pytest.mark.parametrize("url", [
        BILIBILI,
        "https://www.youtube.com/watch?v=abc",
        "https://www.bilibili.com/video/BV1",
    ])
    def test_rejects_other_platforms(self, url):
        """反向对照：名单全空时这些仍然是 False——所以它们不是靠「碰巧不匹配」。"""
        assert douyin_module.is_douyin_url(url) is False, url

    def test_is_case_insensitive(self):
        assert douyin_module.is_douyin_url("https://WWW.DOUYIN.COM/video/1") is True

    def test_survives_a_url_it_cannot_parse(self):
        """不是 URL 的输入不许把判定器带崩——它返回 False，不抛。"""
        assert douyin_module.is_douyin_url("") is False
        assert douyin_module.is_douyin_url("not a url at all") is False


class TestParseRouting:
    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/video/7300000000000000000",
        "https://v.douyin.com/abc123/",
        "https://www.iesdouyin.com/share/video/7300000000000000000",
    ])
    def test_douyin_urls_go_to_the_douyin_parser(self, client_app, parse_calls, url):
        """域名名单里的三种形态都走抖音侧，yt-dlp **零调用**。"""
        client_app.post("/api/parse", json={"url": url})
        assert parse_calls["douyin"] == [url]
        assert parse_calls["ytdlp"] == []

    @pytest.mark.parametrize("url", [BILIBILI, "https://www.youtube.com/watch?v=abc"])
    def test_other_urls_go_to_yt_dlp(self, client_app, parse_calls, url):
        """反向对照：非抖音 URL 必须走 yt-dlp 侧，抖音侧**零调用**。

        两个方向都断才分得清「走反了」与「都没走」。
        """
        client_app.post("/api/parse", json={"url": url})
        assert parse_calls["ytdlp"] == [url]
        assert parse_calls["douyin"] == []

    def test_the_canonical_url_is_what_reaches_the_downstream_call(
        self, client_app, parse_calls
    ):
        """下游拿到的是**归一后**的 URL，不是用户粘贴的整段文案。

        归一只许在 yt-dlp 边界生效：数据库与社区卡片回显仍用原文
        （ADR 0016）。这条断言锁住「归一的结果真的流到了出口」——
        归一函数被调用过不等于它的结果被用上了。
        """
        client_app.post("/api/parse", json={"url": "复制打开 https://v.douyin.com/abc123/ 看看"})
        assert parse_calls["douyin"] == ["https://v.douyin.com/abc123/"]