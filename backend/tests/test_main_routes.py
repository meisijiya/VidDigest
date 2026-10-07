"""`main.py` 两条路由的守卫：/api/direct-url 与 /api/proxy/thumbnail。

这两条此前**路由层零测试**（工单 #46）。`test_ytdlp_boundary_canonical.py`
只直接调 `downloader.get_direct_url`，不经路由——于是 `req.clean_url()`、
`run_in_executor`、异常映射那一层从未被路由测试走到。
`proxy_thumbnail` 则是完全没有任何测试。

判据落在**用户真正读的那个出口**上（HTTP 响应），不测私有函数：
``proxy_thumbnail`` 的地址判定若做成私有 helper，改名换写法都不该让
这些断言变红；反过来，删掉整条路由或把判定删成空操作**必须**转红。

## 代理这一侧为什么值得有守卫

原实现不挂任何鉴权依赖、且 ``url`` 参数零校验，于是任何人可让服务器 GET
任意 URL 并把响应体原样吐回来。收紧的是「**能代理哪些地址**」，
不是「谁能代理」——社区卡片要显示缩略图，公开可读是对的。
"""
import socket

import httpx
import pytest

import main as main_module
from seams import make_client

DIRECT_URL = "/api/direct-url"
THUMBNAIL = "/api/proxy/thumbnail"


# ── 夹具 ────────────────────────────────────────────────────

@pytest.fixture()
def app(db):
    """真实 app + 真实 client。刻意不 `with client`（理由见 test_endpoint_coverage_gaps.py）。"""
    return make_client(main_module.app)


#: 一个**会被放行**的地址（CGNAT 段 100.64.0.0/10）。
#:
#: 为什么不是 TEST-NET（192.0.2.x / 198.51.100.x / 203.0.113.x）：
#: 实测（`.scratch/apply46/probe_ipaddress.py`）这三个文档保留段在
#: Python 3.11 的 ``ipaddress`` 下**全都是 ``is_private == True``**——
#: ``is_private`` 判的是「不是全球可路由地址」，不是 RFC1918 那三段。
#: 第一版用 192.0.2.10，结果反向对照（公网应当照常代理）被自己的守卫拒掉，
#: 红在了它本该绿的那一侧。**地址要实测，别按文档印象选。**
#:
#: 也不能用 8.8.8.8 这类真实公网 IP：那会让「解析」这一步依赖出网。
PUBLIC_IP = "100.64.0.1"

#: 私网/保留地址，逐个对应 ``ipaddress`` 的判定分支。
BLOCKED_IPS = [
    "127.0.0.1",          # loopback
    "10.0.0.1",           # private
    "192.168.1.1",        # private
    "169.254.169.254",    # link-local —— 云环境实例元数据服务
]


# ── 网络边界桩 ────────────────────────────────────────────

def _fake_resolver(monkeypatch, mapping):
    """把 ``socket.getaddrinfo`` 换成固定解析表。

    真实解析不能用在测试里：既要出网，又不稳定。返回形状照抄
    ``getaddrinfo``（``(family, type, proto, canonname, sockaddr)``），
    因为被测代码要真的从里面取 IP。
    """
    def _resolve(host, port, *args, **kwargs):
        ips = mapping.get(host)
        if ips is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
            for ip in ips
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _resolve, raising=True)


def _fake_httpx(monkeypatch, recorder, status=200, content=b"\x89PNG-bytes",
                content_type="image/png"):
    """替换 ``httpx.AsyncClient``，记录真实被请求的 URL 与 Host。

    关键点：桩**不接受**调用方传入的 URL 去「照抄」——它按调用方给的
    目标 IP 应答，并把它记进 ``recorder``。这样「判定发生在解析之后、
    且请求用的是已判定的那个 IP」这件事才能被断言到；
    若实现把**域名**原样丢给 httpx，桩就无从拿到 IP，断言会失败。
    """
    class _Resp:
        def __init__(self):
            self.status_code = status
            self.content = content
            self.headers = {"content-type": content_type}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {self.status_code}", request=None, response=None
                )

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            recorder["url"] = url
            recorder["host"] = kwargs.get("headers", {}).get("Host")
            recorder["headers"] = kwargs.get("headers", {})
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client, raising=True)


# ── POST /api/direct-url ───────────────────────────────────

class TestDirectUrlRoute:
    """路由层守卫。行为本身是对的，这单只补覆盖。"""

    URL = DIRECT_URL

    def test_returns_the_downloader_payload_verbatim(self, app, monkeypatch):
        """直链原样回传——前端靠它拼下载地址，字段被吞就点不动。"""
        payload = {"url": "https://cdn.example/video.mp4", "quality": "1080p"}
        calls = {}

        def fake_get_direct_url(url, format_id):
            calls["args"] = (url, format_id)
            return payload

        monkeypatch.setattr(
            main_module.downloader, "get_direct_url", fake_get_direct_url,
            raising=True,
        )

        r = app.post(self.URL, json={
            "url": "https://cdn.example/video.mp4", "format_id": "137",
        })

        assert r.status_code == 200, r.text
        assert r.json() == {"success": True, "data": payload}, (
            "直链载荷必须原样回传。改字段名 / 少回一个字段时前端会拿到"
            f"一个拼不出地址的响应：{r.json()}"
        )
        assert calls["args"] == ("https://cdn.example/video.mp4", "137"), (
            f"format_id 没有原样传到下载器：{calls['args']}"
        )

    def test_extracts_url_from_share_text(self, app, monkeypatch):
        """分享文案里的 URL 要被抽出来——这是 clean_url 唯一有价值的契约。"""
        seen = {}

        def fake_get_direct_url(url, format_id):
            seen["url"] = url
            return {"url": url}

        monkeypatch.setattr(
            main_module.downloader, "get_direct_url", fake_get_direct_url,
            raising=True,
        )

        share = "复制打开抖音 https://v.douyin.com/abc123/ 看看这个视频"
        r = app.post(self.URL, json={"url": share})

        assert r.status_code == 200, r.text
        assert seen["url"] == "https://v.douyin.com/abc123/", (
            f"分享文案里的 URL 没被抽出来，原样传下去了：{seen['url']!r}"
        )

    def test_downloader_failure_becomes_400(self, app, monkeypatch):
        """上游报错要变成 400 而不是 500——两种都是失败，但语义不同。"""
        def boom(url, format_id):
            raise RuntimeError("上游炸了")

        monkeypatch.setattr(
            main_module.downloader, "get_direct_url", boom, raising=True,
        )

        r = app.post(self.URL, json={"url": "https://cdn.example/v.mp4"})

        assert r.status_code == 400, (
            f"下游失败应当是 400，实得 {r.status_code}；"
            "变成 500 会让前端把「这个视频下不了」误当成「服务器挂了」"
        )
        assert "上游炸了" in r.text, f"错误信息丢了：{r.text}"


# ── GET /api/proxy/thumbnail ───────────────────────────────

class TestThumbnailRejectsInternalAddresses:
    """SSRF：内网与保留地址一律拒绝，且**按解析后的 IP 判**。"""

    URL = THUMBNAIL

    @pytest.mark.parametrize("ip", BLOCKED_IPS)
    def test_blocked_ip_is_refused(self, app, monkeypatch, ip):
        recorder = {}
        _fake_resolver(monkeypatch, {"internal.test": [ip]})
        _fake_httpx(monkeypatch, recorder)

        r = app.get(self.URL, params={"url": "http://internal.test/x.png"})

        assert r.status_code == 400, (
            f"{ip} 应当被拒绝（400），实得 {r.status_code}。"
            "这条被拒之后**不该发出任何请求**——"
            f"桩记录到了 {recorder.get('url')!r}"
        )
        assert "url" not in recorder, (
            f"判定应该在请求之前；实得先发了请求 {recorder.get('url')!r} "
            "再报错（那是 TOCTOU，判定形同虚设）"
        )

    def test_domain_resolving_to_internal_is_refused(self, app, monkeypatch):
        """**这条是判别力所在**：域名看起来无害，解析后是内网。

        只判字面量的实现会放它过关——所以这条必须转红才算守卫有效。
        """
        recorder = {}
        _fake_resolver(monkeypatch, {"sneaky.example": ["169.254.169.254"]})
        _fake_httpx(monkeypatch, recorder)

        r = app.get(self.URL, params={"url": "http://sneaky.example/latest/meta-data"})

        assert r.status_code == 400, (
            "域名形态无害但解析到 169.254.169.254，仍必须拒绝。"
            f"实得 {r.status_code}——这说明判定只看 URL 字面量，"
            "换个域名就能读云实例的元数据"
        )
        assert "url" not in recorder, f"不该发出请求，却发了 {recorder.get('url')!r}"


class TestThumbnailServesPublicAddresses:
    """反向对照：**收紧不能变成全拒**。

    抖音 / 微博 / B 站的图床都在公网。全拒的话上面那几条照样绿，
    而用户看到的是所有缩略图全裂——所以必须有一条真的走通。
    """

    URL = THUMBNAIL

    def test_public_image_is_proxied_verbatim(self, app, monkeypatch):
        recorder = {}
        _fake_resolver(monkeypatch, {"img.example": [PUBLIC_IP]})
        _fake_httpx(
            monkeypatch, recorder,
            content=b"\x89PNG-real-bytes", content_type="image/png",
        )

        r = app.get(self.URL, params={"url": "http://img.example/cover.png"})

        assert r.status_code == 200, (
            f"公网图片应当照常代理，实得 {r.status_code}：{r.text}"
        )
        assert r.content == b"\x89PNG-real-bytes", (
            f"响应体不是上游那份：{r.content!r}"
        )
        assert r.headers["content-type"].startswith("image/png"), (
            f"content-type 没照抄上游：{r.headers.get('content-type')}"
        )

    def test_request_targets_the_resolved_ip_not_the_domain(self, app, monkeypatch):
        """判定之后要用**已判定的那个 IP** 去连。

        这是 DNS rebinding 的防线：判完再把域名丢给客户端解析，
        中间那次解析完全可能给出另一个（内网）地址。
        """
        recorder = {}
        _fake_resolver(monkeypatch, {"img.example": [PUBLIC_IP]})
        _fake_httpx(monkeypatch, recorder)

        r = app.get(self.URL, params={"url": "http://img.example/cover.png"})

        assert r.status_code == 200, r.text
        assert recorder.get("host") == PUBLIC_IP, (
            "请求应当指向已判定过的那个 IP（Host 头带上它），"
            f"实得 {recorder.get('host')!r}——把域名原样交出去的话，"
            "判定与连接之间那个窗口就还能被 rebinding 挤进去"
        )

    def test_non_image_content_type_is_still_proxied(self, app, monkeypatch):
        """content-type 照抄上游：CDN 回错类型时不该被本地改写。"""
        recorder = {}
        _fake_resolver(monkeypatch, {"img.example": [PUBLIC_IP]})
        _fake_httpx(
            monkeypatch, recorder, content=b"GIF89a", content_type="image/gif",
        )

        r = app.get(self.URL, params={"url": "http://img.example/a.gif"})

        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/gif"), (
            f"content-type 被本地改写了：{r.headers.get('content-type')}"
        )


class TestThumbnailUpstreamFailures:
    """上游 4xx / 5xx 一律变 502，且**不能把上游状态码原样透出去**。"""

    URL = THUMBNAIL

    @pytest.mark.parametrize("status", [404, 403, 500, 503])
    def test_upstream_error_becomes_502(self, app, monkeypatch, status):
        recorder = {}
        _fake_resolver(monkeypatch, {"img.example": [PUBLIC_IP]})
        _fake_httpx(monkeypatch, recorder, status=status)

        r = app.get(self.URL, params={"url": "http://img.example/missing.png"})

        assert r.status_code == 502, (
            f"上游 {status} 应当变成 502，实得 {r.status_code}"
        )

    def test_oversized_response_is_refused(self, app, monkeypatch):
        """超限响应不该被整块透出去——那是「一次请求吃满内存」那条路。"""
        recorder = {}
        _fake_resolver(monkeypatch, {"img.example": [PUBLIC_IP]})
        _fake_httpx(monkeypatch, recorder, content=b"x" * (9 * 1024 * 1024))

        r = app.get(self.URL, params={"url": "http://img.example/huge.png"})

        assert r.status_code == 502, (
            f"超过大小上限应当是 502，实得 {r.status_code}；"
            "透出去就等于让任意人用一次请求占满内存"
        )


class TestThumbnailRejectsUnusableAddresses:
    """地址本身不可用（解析不出来）→ 400，且一个请求都不发。

    与上游失败分开成两个类：解析失败判的是**地址**，上游 5xx 判的是**响应**，
    两者状态码不同（400 / 502）。塞进同一个类会让人以为它们同源。
    """

    URL = THUMBNAIL

    def test_unresolvable_host_is_refused_before_any_request(self, app, monkeypatch):
        recorder = {}
        _fake_resolver(monkeypatch, {})  # 任何域名都解析失败
        _fake_httpx(monkeypatch, recorder)

        r = app.get(self.URL, params={"url": "http://nope.invalid/x.png"})

        assert r.status_code == 400, (
            f"解析失败应当是 400（无效地址），实得 {r.status_code}"
        )
        assert "url" not in recorder, (
            f"解析都没成功，不该发出请求，却发了 {recorder.get('url')!r}"
        )

    def test_non_http_scheme_is_refused(self, app, monkeypatch):
        """file:// 与 ftp:// 同样拒绝——它们不走 HTTP 客户端，原路径会炸。"""
        recorder = {}
        _fake_httpx(monkeypatch, recorder)

        r = app.get(self.URL, params={"url": "file:///etc/passwd"})

        assert r.status_code == 400, (
            f"file:// 应当被拒（400），实得 {r.status_code}"
        )
        assert "url" not in recorder, f"不该发出请求，却发了 {recorder.get('url')!r}"
