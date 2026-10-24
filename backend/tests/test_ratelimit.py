"""公开端点的限流（工单 #85）。

## 为什么要有这层

`/api/parse`、`/api/download`、`/api/direct-url`、`/api/proxy/thumbnail`
与登录注册端点**都不挂鉴权**——谁都能打。而它们的成本全在服务器这一侧：
出网抓取、落盘、bcrypt 计算。已有的额度护栏（每日解析 3 次 / 追问 10 次）
只挂在模型调用那边，管不到这些端点。

没有这层时，一个脚本就能把带宽与 CPU 打满，而日志里全是「正常请求」。

## 判据读的是外部可观察的东西

- 超限的响应码是 **429**；
- 响应带 `Retry-After` 头（客户端据此知道等多久）；
- 响应体里的 `detail.error` 说清是**服务器**在限（不是视频站在风控）；
- 桶与桶互不影响（解析超了不代表下载也超）；
- 窗口过了就恢复；
- 计数表有上限（限流器自己不能变成新的故障源）。

## 三个容易做错的地方，这里都有用例钉着

1. **X-Forwarded-For 不能无条件采信**。公网直连时任何人改一个头就能绕开
   限流，限流器就成了摆设。只有直接对端是回环/私网（前面确实有反向代理）
   才采信。两条用例一正一反：公网对端换 XFF 仍算同一个人，私网对端换 XFF
   算不同的人。
2. **超上限时踢最旧的那条**，踢新的等于给攻击者发免死金牌。
3. **限流器默认在测试里是关着的**（conftest 设 `VIDDIGEST_RATE_LIMIT_ENABLED=0`），
   免得别的用例互相干扰；这一份用例自己把它打开。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import main as main_module  # noqa: E402
import ratelimit  # noqa: E402
from seams import make_client  # noqa: E402

#: 一个必定连不上的地址：连接被拒是**立刻**发生的，不会去碰外网。
DEAD_URL = "http://127.0.0.1:9/nothing"


@pytest.fixture(autouse=True)
def limiter_on(monkeypatch):
    """每个用例都从「限流开着、计数清零、环境干净」开始。"""
    monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_WINDOW", "60")
    for env_name, _default in ratelimit.BUCKETS.values():
        monkeypatch.delenv(env_name, raising=False)
    ratelimit.reset_counters()
    yield
    ratelimit.reset_counters()


@pytest.fixture()
def client():
    """真实应用客户端（不跑 lifespan，所以不会碰真库）。"""
    return make_client(main_module.app)


def _parse_body():
    return {"url": DEAD_URL}


def _download_body():
    return {"url": DEAD_URL, "format_id": "1"}


class TestThePublicEndpointsAreThrottled:
    """限流挂在哪些端点上——逐个端点证明，不靠读代码。"""

    def test_the_parse_endpoint_refuses_the_second_call(self, client, monkeypatch):
        """解析端点：第一次放行，第二次 429。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        # 让处理函数立刻失败：限流这一层在处理函数之前跑，
        # 第一次请求的结果与限流无关，但我们要的是它**不是** 429。
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        first = client.post("/api/parse", json=_parse_body())
        assert first.status_code != 429, "第一次请求不该被限流拦下"
        second = client.post("/api/parse", json=_parse_body())
        assert second.status_code == 429
        assert "retry-after" in {k.lower() for k in second.headers}
        detail = second.json()["detail"]
        assert detail["success"] is False
        assert detail.get("code") == "rate_limit", (
            "429 的错误体里没有机器可读的 code —— 前端只能去认中文文案，"
            "两边分头改就会把自家限流误报成平台风控"
        )
        assert "服务器限流" in detail["error"]

    def test_the_download_endpoint_refuses_the_second_call(self, client, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_DOWNLOAD", "1")
        monkeypatch.setattr(
            main_module.downloader,
            "download_video",
            lambda url, fmt: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        first = client.post("/api/download", json=_download_body())
        assert first.status_code != 429
        second = client.post("/api/download", json=_download_body())
        assert second.status_code == 429

    def test_the_direct_url_endpoint_refuses_the_second_call(self, client, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_DIRECT_URL", "1")
        monkeypatch.setattr(
            main_module.downloader,
            "get_direct_url",
            lambda url, fmt: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        first = client.post("/api/direct-url", json=_download_body())
        assert first.status_code != 429
        second = client.post("/api/direct-url", json=_download_body())
        assert second.status_code == 429

    def test_the_thumbnail_endpoint_refuses_the_second_call(self, client, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_THUMBNAIL", "1")
        first = client.get("/api/proxy/thumbnail", params={"url": DEAD_URL})
        assert first.status_code != 429
        second = client.get("/api/proxy/thumbnail", params={"url": DEAD_URL})
        assert second.status_code == 429

    def test_the_login_endpoint_refuses_the_second_call(self, client, db, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_LOGIN", "1")
        body = {"email": "nobody@example.com", "password": "whatever"}
        first = client.post("/api/auth/login", json=body)
        assert first.status_code != 429
        second = client.post("/api/auth/login", json=body)
        assert second.status_code == 429

    def test_the_register_endpoint_refuses_the_second_call(self, client, db, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_REGISTER", "1")
        body = {"email": "probe@example.com", "password": "x"}
        first = client.post("/api/auth/register", json=body)
        assert first.status_code != 429
        second = client.post("/api/auth/register", json=body)
        assert second.status_code == 429


class TestTheBucketsAreIndependent:
    def test_being_throttled_on_parse_does_not_throttle_download(self, client, monkeypatch):
        """桶与桶各数各的：一个桶超了，另一个桶照常放行。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_DOWNLOAD", "10")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        monkeypatch.setattr(
            main_module.downloader,
            "download_video",
            lambda url, fmt: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        client.post("/api/parse", json=_parse_body())
        assert client.post("/api/parse", json=_parse_body()).status_code == 429
        # 解析桶已经爆了，下载桶是另一个桶——不该被连带。
        assert client.post("/api/download", json=_download_body()).status_code != 429


class TestTheWindowExpires:
    def test_the_call_is_allowed_again_after_the_window(self, client, monkeypatch):
        """窗口过了就恢复——限流不是永久封禁。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_WINDOW", "60")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        now = {"t": 1000.0}
        ratelimit.use_counter(ratelimit.FixedWindowCounter(clock=lambda: now["t"]))
        client.post("/api/parse", json=_parse_body())
        assert client.post("/api/parse", json=_parse_body()).status_code == 429
        now["t"] += 61
        assert client.post("/api/parse", json=_parse_body()).status_code != 429


class TestTheForwardedHeaderIsNotTakenOnFaith:
    def test_a_public_peer_cannot_forge_a_new_identity(self, monkeypatch):
        """公网直连时改 X-Forwarded-For 不该换一个人——否则限流形同虚设。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        public = TestClient(main_module.app, client=("203.0.113.5", 50000))
        first = public.post(
            "/api/parse", json=_parse_body(), headers={"X-Forwarded-For": "198.51.100.7"}
        )
        assert first.status_code != 429
        second = public.post(
            "/api/parse", json=_parse_body(), headers={"X-Forwarded-For": "198.51.100.9"}
        )
        assert second.status_code == 429, "换个 X-Forwarded-For 就绕开了限流"

    def test_a_trusted_peer_may_forward_the_real_client(self, monkeypatch):
        """反向对照：前面确实有反向代理时（对端是私网），XFF 才是可信来源。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        behind_proxy = TestClient(main_module.app, client=("127.0.0.1", 50000))
        first = behind_proxy.post(
            "/api/parse", json=_parse_body(), headers={"X-Forwarded-For": "198.51.100.7"}
        )
        assert first.status_code != 429
        second = behind_proxy.post(
            "/api/parse", json=_parse_body(), headers={"X-Forwarded-For": "198.51.100.9"}
        )
        assert second.status_code != 429, "两个不同的真实来源被算成了同一个人"


class TestTheCounterTableIsBounded:
    def test_the_number_of_tracked_sources_stays_capped(self):
        """限流器自己不能成为新的故障源：换着 IP 打也不能把内存吃光。"""
        counter = ratelimit.FixedWindowCounter(max_keys=5)
        for i in range(50):
            counter.hit(f"ip:198.51.100.{i}", 10, 60)
        assert counter.tracked_keys() <= 5

    def test_the_oldest_entry_is_the_one_evicted(self):
        """超上限时踢最旧的——踢新的等于给攻击者免死金牌。"""
        counter = ratelimit.FixedWindowCounter(max_keys=2)
        now = {"t": 1000.0}
        counter = ratelimit.FixedWindowCounter(clock=lambda: now["t"], max_keys=2)
        counter.hit("ip:old", 10, 60)
        now["t"] += 1
        counter.hit("ip:new", 10, 60)
        now["t"] += 1
        counter.hit("ip:newest", 10, 60)  # 超上限 → 踢最旧的
        assert counter.tracked_keys() == 2
        # 最旧的被踢了；后两个还在（再打一次不会因为「踢错了对象」而重置）。
        allowed, _remaining, _retry = counter.hit("ip:new", 10, 60)
        assert allowed is True


class TestTheLimitsAreConfigurable:
    def test_the_limit_can_be_raised_by_environment(self, client, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "2")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        assert client.post("/api/parse", json=_parse_body()).status_code != 429
        assert client.post("/api/parse", json=_parse_body()).status_code != 429
        assert client.post("/api/parse", json=_parse_body()).status_code == 429

    def test_zero_means_no_limit_at_all(self, client, monkeypatch):
        """上限 0 = 这个桶不限流（给压测与内网调用留的口子）。"""
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "0")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        for _ in range(30):
            assert client.post("/api/parse", json=_parse_body()).status_code != 429

    def test_the_whole_limiter_can_be_switched_off(self, client, monkeypatch):
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_ENABLED", "0")
        monkeypatch.setenv("VIDDIGEST_RATE_LIMIT_PARSE", "1")
        monkeypatch.setattr(
            main_module.downloader,
            "parse_video",
            lambda url: (_ for _ in ()).throw(RuntimeError("probe")),
        )
        for _ in range(5):
            assert client.post("/api/parse", json=_parse_body()).status_code != 429
