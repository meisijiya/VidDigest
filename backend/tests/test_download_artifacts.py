"""下载产物的生命周期：用完即删、失败不留半截（工单 #90）。

## 判据读的是外部可观察的东西

- 响应体里有没有内容（产物在发送时还是完整的）
- 磁盘上还有没有那个文件（用完即删）
- 失败时磁盘上有没有半截文件
- 客户端断开时磁盘上还有没有那个文件
- 端点是不是还被限流挡着

不测私有函数，不断言内部调用顺序。

## 为什么清理不能写在 handler 的 finally 里

响应体就是从产物文件里流出去的。文件先删，响应就只剩 0 字节——用户拿到
一个能打开、但是空的视频。所以清理必须挂在**响应发完之后**
（`test_the_artifact_is_still_there_when_the_response_finishes` 钉的就是
这个先后顺序）。

## 为什么清理不能挂在框架注入的后台任务上

客户端中途断开时，响应没发完，后台任务那一句根本不执行——产物就永久留在
盘上了。清理必须挂在响应自己的 `finally` 上：发完了删，断开了也删
（`test_the_artifact_is_removed_even_when_the_client_disconnects`）。

## 清理的范围是「这次下载自己写下的路径」

下载目录是共享的，别的请求正在往里写。按「目录里所有新文件都删」或
「按时间窗口扫目录」来清，会删掉别人的在途文件——把「残留占地方」换成
「下载坏掉」。所以这里钉的是：别的请求的文件（mtime 更早的、更晚的
都算）一个都不许动。

## 两条通道，一套清理

通用 yt-dlp 与抖音写进同一个目录，清理口径必须只有一份。这里用同一个
用例跑两条通道，证明它们的行为一致，而不是各写一条断言各说各话。
"""
import fnmatch
import os
import time
from pathlib import Path

import pytest

import douyin as douyin_module
import downloader as downloader_module
import main as main_module
import ratelimit
from seams import make_client

BILIBILI = "https://www.bilibili.com/video/BV163aC6iEc5"
DOUYIN = "https://www.douyin.com/video/7300000000000000000"
REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def download_dir(monkeypatch, tmp_path):
    """把两条通道的产物目录都指到临时目录。

    真目录是 ``backend/downloads``，测试往那儿写文件会污染工作树。
    端点失败时的清理由 ``downloader.DOWNLOAD_DIR`` 决定，所以两处都要改。
    """
    monkeypatch.setattr(main_module.downloader, "DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(main_module.douyin_parser, "download_dir", tmp_path)
    return tmp_path


@pytest.fixture()
def client(db):
    """真实 app + 真实 client。刻意不 ``with client``（会跑 lifespan 重置 db 夹具）。

    为什么关掉 ``raise_server_exceptions``：服务端炸了应该表现为一个 500
    响应，而不是把异常从测试里抛出来。判据看的是 HTTP 码——「实现坏掉」
    和「判据不成立」在报告里长得一样，才分得清是谁的问题。
    """
    return make_client(main_module.app, raise_server_exceptions=False)


def _writer(artifact, payload=b"real-bytes"):
    """造一个真写文件的下载桩：产物在磁盘上真的存在过。"""

    def fake_download(url, format_id):
        artifact.write_bytes(payload)
        return {"filepath": str(artifact), "filename": artifact.name}

    return fake_download


class _ArtifactWatch:
    """记录「响应体发完的那一刻，产物还在不在盘上」。

    为什么要专门看这一瞬间：清理挂早一点，文件就没了，响应会变成 0 字节。
    只看「请求结束后文件没了」分不出「响应发完才删」和「返回响应前就删」——
    后者会当场把响应打成空的。
    """

    def __init__(self, app, path):
        self.app = app
        self.path = path
        self.exists_when_response_finished = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if (message["type"] == "http.response.body"
                    and not message.get("more_body", False)):
                self.exists_when_response_finished = os.path.exists(self.path)
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── 1. 用完即删 ──────────────────────────────────────────────


def test_the_body_is_complete_and_the_artifact_is_gone_after_the_response(
    monkeypatch, download_dir, client
):
    """下载到的是完整文件，响应发完它就不在盘上了。"""
    artifact = download_dir / "ok.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 200
    assert r.content == b"real-bytes", (
        "响应体是空的 —— 产物在发送时被清掉了，用户拿到的是 0 字节文件"
    )
    assert not artifact.exists(), "响应结束后产物还在盘上，没有用完即删"


def test_the_artifact_is_still_there_when_the_response_finishes(
    monkeypatch, download_dir
):
    """清理挂在响应**发完之后**，不是返回响应之前。"""
    artifact = download_dir / "ok.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )
    watch = _ArtifactWatch(main_module.app, artifact)
    watching_client = make_client(watch, raise_server_exceptions=False)

    r = watching_client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 200
    assert r.content == b"real-bytes"
    assert watch.exists_when_response_finished is True, (
        "响应体发完时产物已经不在了 —— 清理挂在了响应之前"
    )
    assert not artifact.exists()


def test_a_failing_cleanup_does_not_break_the_response(
    monkeypatch, download_dir, client
):
    """清理失败不该把一次成功的下载变成失败。"""
    artifact = download_dir / "ok.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )
    real_remove = os.remove

    def fake_remove(path, *args, **kwargs):
        if str(path) == str(artifact):
            raise OSError("设备忙，删不掉")
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", fake_remove)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 200
    assert r.content == b"real-bytes"
    assert artifact.exists(), "清理失败时产物应当留在盘上，等巡检按超龄收走"


# ── 2. 失败不留半截 ──────────────────────────────────────────


class _FakeYoutubeDL:
    """假 yt-dlp：按真流程写下一个临时文件，然后失败。

    为什么不去桩 `download_video`：失败清理住在**真实通道**里——yt-dlp 的
    进度钩子报出它写到哪儿了，失败时按这份清单清。桩掉 `download_video`
    就把这段逻辑整个绕过去了，测的就不是它。
    """

    def __init__(self, opts, download_dir, title="broken"):
        self._opts = opts
        self._tmp = os.path.join(str(download_dir), f"{title}.mp4.part")
        self._final = os.path.join(str(download_dir), f"{title}.mp4")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def extract_info(self, url, download=True):
        with open(self._tmp, "wb") as handle:
            handle.write(b"half")
        # 真 yt-dlp 下载中会报「正在写哪个临时文件」——失败清理就靠这条信息。
        for hook in self._opts.get("progress_hooks", []):
            hook({
                "status": "downloading",
                "filename": self._final,
                "tmpfilename": self._tmp,
            })
        raise RuntimeError("网络断了")


def _failing_ytdlp(monkeypatch, download_dir):
    """让真实通道走一次「写了一半就断」的下载。"""
    monkeypatch.setattr(
        downloader_module.yt_dlp,
        "YoutubeDL",
        lambda opts: _FakeYoutubeDL(opts, download_dir),
    )


def test_a_failed_download_leaves_no_partial_file(
    monkeypatch, download_dir, client
):
    """下载失败：半截文件不许留在盘上。"""
    _failing_ytdlp(monkeypatch, download_dir)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 400
    assert r.json()["detail"]["error"].startswith("下载失败")
    assert not (download_dir / "broken.mp4.part").exists(), (
        "下载失败后半截文件还留在盘上"
    )


@pytest.mark.parametrize("mtime_offset", [-3600, 0.5], ids=["earlier", "later"])
def test_a_failed_download_does_not_touch_files_it_did_not_create(
    monkeypatch, download_dir, client, mtime_offset
):
    """别人的在途文件不能被这次失败顺手删掉。

    反向对照：清理的范围是「这次下载自己写下的路径」，不是「目录里所有
    新文件」，也不是「某个时间窗口里出现过的文件」。

    mtime 更晚的那种更要盖住：别的请求刚开始写，mtime 比这次失败还新——
    按时间窗口扫目录的实现会把它删掉。
    """
    in_flight = download_dir / "someone-else.mp4.part"
    in_flight.write_bytes(b"in flight")
    stamp = time.time() + mtime_offset
    os.utime(in_flight, (stamp, stamp))

    _failing_ytdlp(monkeypatch, download_dir)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 400
    assert in_flight.exists(), "这次失败把别人的在途文件删了"


# ── 3. 同名产物：不删别人的 ───────────────────────────────────


class _TakeoverDuringResponse:
    """在响应体开始发出的那一刻，把产物改写成别人的内容。

    为什么要在响应**发送中**改：产物名不带唯一 id，两个请求下同一个视频
    会落到同一个路径上，后到的会盖掉先到的。真实场景就是这样——先到的
    那个请求正在把文件回传，后到的那个把同一个路径盖了。
    """

    def __init__(self, app, path, payload=b"another-request-bytes"):
        self.app = app
        self.path = path
        self.payload = payload
        self.took_over = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.body" and not self.took_over:
                self.took_over = True
                with open(self.path, "wb") as handle:
                    handle.write(self.payload)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def test_the_cleanup_skips_a_file_someone_else_took_over(
    monkeypatch, download_dir
):
    """同名产物被别人盖掉之后，这次的清理不许把它删掉。

    产物名不带唯一 id：两个请求下同一个视频会落到同一个路径上。这次请求
    的清理如果还按「这个路径是我的」去删，删掉的就是另一个请求的产物——
    另一个用户拿到 404。
    """
    artifact = download_dir / "shared-name.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )
    takeover = _TakeoverDuringResponse(main_module.app, artifact)
    client = make_client(takeover, raise_server_exceptions=False)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 200
    assert artifact.exists(), "这次清理把别人的产物删了"


# ── 4. 客户端断开：照样要清 ──────────────────────────────────


class _DisconnectOnFirstBody:
    """模拟客户端在响应体还没发完的时候就断开。"""

    def __init__(self, app):
        self.app = app
        self.cut = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.body" and not self.cut:
                self.cut = True
                raise RuntimeError("client gone")
            await send(message)

        await self.app(scope, receive, send_wrapper)


def test_the_artifact_is_removed_even_when_the_client_disconnects(
    monkeypatch, download_dir
):
    """客户端中途断开：产物照样要清掉。

    挂在框架注入的后台任务上是不够的：starlette 的 `FileResponse.__call__`
    末尾那句 `await self.background()` 不在 try/finally 里，响应没发完就
    抛异常时它根本不执行，产物就永久留在盘上了。
    """
    artifact = download_dir / "ok.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )
    cut = _DisconnectOnFirstBody(main_module.app)
    client = make_client(cut, raise_server_exceptions=False)

    client.post("/api/download", json={"url": BILIBILI})

    assert cut.cut is True, "响应体没被截断 —— 前置条件不成立（断连没发生）"
    assert not artifact.exists(), "客户端断开了，产物还留在盘上"


def test_the_douyin_channel_removes_its_own_half_written_file(monkeypatch, tmp_path):
    """抖音通道自己清自己写的半截文件。

    抖音那条路直写最终文件名，所以它自己知道半截文件叫什么；通用通道靠
    yt-dlp 的进度钩子报路径。两条路各自清自己写下的路径，口径是同一份
    （`backend/download_artifacts.py`）。
    """
    target = tmp_path / "clip.mp4"

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            yield b"first-half"
            raise RuntimeError("连接被重置")

    monkeypatch.setattr(
        douyin_module.requests, "get", lambda *a, **kw: _FakeResponse()
    )

    with pytest.raises(RuntimeError):
        douyin_module.DouyinParser._download_file("http://example/clip.mp4", target)

    assert not target.exists(), "抖音通道写了一半就断了，文件还留在盘上"


def test_the_douyin_channel_keeps_a_finished_file(monkeypatch, tmp_path):
    """反向对照：写完了的文件不许被清掉。"""
    target = tmp_path / "clip.mp4"

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=8192):
            yield b"whole"

    monkeypatch.setattr(
        douyin_module.requests, "get", lambda *a, **kw: _FakeResponse()
    )

    douyin_module.DouyinParser._download_file("http://example/clip.mp4", target)

    assert target.exists()
    assert target.read_bytes() == b"whole"


# ── 5. 两条通道，一套清理 ────────────────────────────────────


@pytest.mark.parametrize("url", [BILIBILI, DOUYIN], ids=["yt-dlp", "douyin"])
def test_both_channels_get_the_same_cleanup(
    monkeypatch, download_dir, client, url
):
    """两条通道写进同一个目录，清理口径必须是同一套。"""
    artifact = download_dir / "shared.mp4"
    monkeypatch.setattr(
        main_module.downloader, "download_video", _writer(artifact)
    )
    monkeypatch.setattr(
        main_module.douyin_parser, "download", _writer(artifact)
    )

    r = client.post("/api/download", json={"url": url})

    assert r.status_code == 200
    assert r.content == b"real-bytes"
    assert not artifact.exists(), "这条通道的产物没有被清掉"


# ── 6. 限流闸门不变 ──────────────────────────────────────────


def test_the_download_endpoint_is_still_rate_limited(monkeypatch, download_dir, client):
    """产物治理不该顺手改掉限流：还是 10 次/分钟，超了回 429。"""
    monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_ENABLED", "1")
    ratelimit.reset_counters()

    def fake_download(url, format_id):
        raise RuntimeError("网络断了")

    monkeypatch.setattr(main_module.downloader, "download_video", fake_download)

    statuses = [
        client.post("/api/download", json={"url": BILIBILI}).status_code
        for _ in range(11)
    ]

    assert statuses[:10] == [400] * 10, statuses
    assert statuses[10] == 429, (
        f"第 11 次请求没有被限流挡下（{statuses}）—— 产物治理动到了限流闸门"
    )


# ── 7. 构建上下文不含下载产物 ────────────────────────────────


def _ignore_patterns(path):
    """把 .dockerignore 读成一组规则（跳过空行与注释）。"""
    patterns = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _rule_matches(rule, rel_path):
    """判断一条忽略规则是否命中一个相对路径。

    只实现这个文件里真用到的形态：目录前缀（``data/``）、精确名
    （``.env``）、简单通配（``*.pyc``）。
    """
    if rule.endswith("/"):
        return rel_path.startswith(rule)
    if "*" in rule or "?" in rule:
        return fnmatch.fnmatch(rel_path, rule)
    head = rel_path.split("/")[0]
    return rel_path == rule or head == rule


def _excluded(patterns, rel_path):
    """这个相对路径会不会被这些规则排除（后出现的规则覆盖先出现的）。"""
    excluded = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        rule = pattern[1:] if negated else pattern
        if _rule_matches(rule, rel_path):
            excluded = not negated
    return excluded


def test_the_build_context_excludes_the_download_dir():
    """构建上下文里不能有下载产物（工单 #90）。

    断言的是「规则集合里有能挡掉 downloads/ 的规则」，不是「文件里出现过
    downloads/ 这几个字母」：后者挡不住把规则改写成 download/ 的等价改写。

    真正证明它的是 CI 里那条「镜像里查不到下载残留」——单测只能证明规则
    写着，证明 docker 照做的是 CI。
    """
    patterns = _ignore_patterns(REPO / "backend" / ".dockerignore")

    assert _excluded(patterns, "downloads/leftover.mp4"), patterns
    assert _excluded(patterns, "downloads/sub/leftover.mp4"), patterns


def test_the_build_context_still_keeps_the_runtime_code():
    """反向对照：这条规则不许把整个上下文都挡掉。"""
    patterns = _ignore_patterns(REPO / "backend" / ".dockerignore")

    assert not _excluded(patterns, "main.py"), patterns
    assert not _excluded(patterns, "requirements.txt"), patterns
