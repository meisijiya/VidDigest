"""下载产物的生命周期：用完即删、失败不留半截（工单 #90）。

## 判据读的是外部可观察的东西

- 响应体里有没有内容（产物在发送时还是完整的）
- 磁盘上还有没有那个文件（用完即删）
- 失败时磁盘上有没有半截文件
- 端点是不是还被限流挡着

不测私有函数，不断言内部调用顺序。

## 为什么清理不能写在 handler 的 finally 里

响应体就是从产物文件里流出去的。文件先删，响应就只剩 0 字节——用户拿到
一个能打开、但是空的视频。所以清理必须挂在**响应发完之后**
（`test_the_artifact_is_still_there_when_the_response_finishes` 钉的就是
这个先后顺序）。

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
    """真实 app + 真实 client。刻意不 ``with client``（会跑 lifespan 重置 db 夹具）。"""
    return make_client(main_module.app)


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
    watching_client = make_client(watch)

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


def test_a_failed_download_leaves_no_partial_file(
    monkeypatch, download_dir, client
):
    """下载失败：半截文件不许留在盘上。"""
    partial = download_dir / "broken.mp4.part"

    def fake_download(url, format_id):
        partial.write_bytes(b"half")
        raise RuntimeError("网络断了")

    monkeypatch.setattr(main_module.downloader, "download_video", fake_download)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 400
    assert r.json()["detail"]["error"].startswith("下载失败")
    assert not partial.exists(), "下载失败后半截文件还留在盘上"


def test_a_failed_download_does_not_touch_files_it_did_not_create(
    monkeypatch, download_dir, client
):
    """别人的在途文件不能被这次失败顺手删掉。

    反向对照：清理的范围是「这次请求开始之后出现的半截文件」，
    不是「目录里所有新文件」。把范围放大就是删别人的下载。
    """
    in_flight = download_dir / "someone-else.mp4.part"
    in_flight.write_bytes(b"in flight")
    old = time.time() - 3600
    os.utime(in_flight, (old, old))

    def fake_download(url, format_id):
        raise RuntimeError("网络断了")

    monkeypatch.setattr(main_module.downloader, "download_video", fake_download)

    r = client.post("/api/download", json={"url": BILIBILI})

    assert r.status_code == 400
    assert in_flight.exists(), "这次失败把别人早就开始的下载产物删了"


def test_the_douyin_channel_removes_its_own_half_written_file(monkeypatch, tmp_path):
    """抖音通道自己清自己写的半截文件。

    抖音那条路直写最终文件名，所以它自己知道半截文件叫什么；
    端点只知道「有个 .part 残留」，认不出这种文件。
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


# ── 3. 两条通道，一套清理 ────────────────────────────────────


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


# ── 4. 限流闸门不变 ──────────────────────────────────────────


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


# ── 5. 构建上下文不含下载产物 ────────────────────────────────


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
