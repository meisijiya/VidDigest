"""按调用方限流：给**不挂鉴权**的公开端点补一层护栏。

为什么要有这层：`/api/parse`、`/api/download`、`/api/direct-url`、
`/api/proxy/thumbnail` 与登录注册端点都不挂鉴权，谁都能打；而它们的
成本全在服务器这一侧（出网抓取、落盘、bcrypt 计算）。额度护栏只挂在
模型调用那边（每日解析 / 追问两个计数器），管不到这些端点——没有这一层，
一个脚本就能把带宽与 CPU 打满。

怎么算「同一个人」：未登录请求按来源 IP 分桶。来源 IP 取自**直接对端**；
只有当直接对端是回环或私网地址（说明这台机器前面确实有个反向代理，
本仓的 nginx 就是这种）才采信 `X-Forwarded-For` 的第一个地址。
公网直连时**不采信**该头：否则任何人改一个头就能绕开限流，
限流器就成了摆设。

为什么用固定窗口而不是滑动窗口或令牌桶：滑动窗口要维护时间序列，
令牌桶要处理突发与补充速率——这两样都要写一堆难解释的规则。固定窗口
的语义一句话说得清（「一分钟内最多 N 次」），`Retry-After` 也能直接
给出窗口剩余秒数。够用，且口径好解释。

范围：这是**单机**限流。进程重启计数就清零，多实例之间各数各的——
现在后端是单进程单 worker（`docs/TECHNICAL.md` §1.3），多实例部署那天
再换成共享存储。
"""

import math
import os
import threading
import time
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request

#: 可信对端的网段（回环、私网、链路本地）。
#:
#: 逐条列出来而不是用 `ipaddress.is_private`：Python 把文档保留段
#: （192.0.2.0/24 等）也算私有，那不是「部署里会出现的对端」。
_TRUSTED_NETWORKS = tuple(
    ip_network(net) for net in (
        "127.0.0.0/8", "::1/128",
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "fe80::/10", "fc00::/7",
    )
)

#: 计数表最多记多少个来源。
#:
#: 为什么要有上限：限流器自己不能成为新的故障源——攻击者换着 IP 打，
#: 每一分钟塞进来一批新键，内存就被这些键吃掉了。过期条目会被清理，
#: 但清理之前上限先兜住。
MAX_TRACKED_KEYS = 10000

#: 每个桶：(环境变量名, 默认上限)。上限 0 表示这个桶不限流。
#:
#: 默认值的取法：按「这个端点一次要花多少服务器资源」排——
#: 下载要落盘（最重）→ 10 次/分钟；解析要出网抓取 → 20 次/分钟；
#: 直链与缩略图是轻量出网 → 30 / 60 次/分钟；登录要算 bcrypt → 10 次/分钟；
#: 注册要写库 → 5 次/分钟。都可用环境变量改，改不动就说明该换部署形态了。
BUCKETS = {
    "/api/parse": ("VIDDIGEST_RATE_LIMIT_PARSE", 20),
    "/api/download": ("VIDDIGEST_RATE_LIMIT_DOWNLOAD", 10),
    "/api/direct-url": ("VIDDIGEST_RATE_LIMIT_DIRECT_URL", 30),
    "/api/proxy/thumbnail": ("VIDDIGEST_RATE_LIMIT_THUMBNAIL", 60),
    "/api/login": ("VIDDIGEST_RATE_LIMIT_LOGIN", 10),
    "/api/register": ("VIDDIGEST_RATE_LIMIT_REGISTER", 5),
}


class FixedWindowCounter:
    """固定窗口计数器：同一个键在一个窗口内最多记 limit 次。

    线程安全靠一把锁——FastAPI 的同步依赖跑在线程池里，
    `dict` 的「读-改-写」不是原子的，不加锁会漏计。
    """

    def __init__(self, clock=time.monotonic, max_keys: int = MAX_TRACKED_KEYS):
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        # 键 → [窗口起点, 次数]。用 list 而不是 tuple 是因为要就地改。
        self._entries: dict[str, list[float]] = {}

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int, int]:
        """记一次。

        返回 `(是否放行, 剩余次数, Retry-After 秒数)`。
        """
        now = self._clock()
        with self._lock:
            self._prune(now, window)
            entry = self._entries.get(key)
            if entry is None or now - entry[0] >= window:
                if len(self._entries) >= self._max_keys:
                    self._evict_oldest()
                self._entries[key] = [now, 1]
                return True, max(limit - 1, 0), 0
            entry[1] += 1
            if entry[1] > limit:
                retry_after = max(int(math.ceil(window - (now - entry[0]))), 1)
                return False, 0, retry_after
            return True, limit - entry[1], 0

    def tracked_keys(self) -> int:
        """当前记着多少个来源（测试与巡检用）。"""
        with self._lock:
            return len(self._entries)

    def reset(self) -> None:
        """清空计数（测试用；也留给将来加「运维手动清一次」的入口）。"""
        with self._lock:
            self._entries.clear()

    def _prune(self, now: float, window: int) -> None:
        """删掉窗口已过的条目——不删的话计数表只增不减。"""
        stale = [k for k, v in self._entries.items() if now - v[0] >= window]
        for k in stale:
            del self._entries[k]

    def _evict_oldest(self) -> None:
        """超上限时踢掉窗口起点最早的那一条。

        踢最旧而不是踢新的：踢新的等于「新来的不计数」，
        那等于给攻击者发了一张免死金牌。
        """
        oldest_key = None
        oldest_start = None
        for k, v in self._entries.items():
            if oldest_start is None or v[0] < oldest_start:
                oldest_key = k
                oldest_start = v[0]
        if oldest_key is not None:
            del self._entries[oldest_key]


#: 进程内唯一的计数器。
_counter = FixedWindowCounter()


def reset_counters() -> None:
    """清空计数（测试与将来的人工清理都用它）。"""
    _counter.reset()


def use_counter(counter: FixedWindowCounter) -> None:
    """换一个计数器（测试用；也给将来「把计数存到 Redis」的改造留了位置）。"""
    global _counter
    _counter = counter


def tracked_keys() -> int:
    """当前记着多少个来源（测试与巡检用）。"""
    return _counter.tracked_keys()


def _env_int(name: str, default: int) -> int:
    """读一个整数环境变量，取不到或不是数字时用默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _enabled() -> bool:
    """总开关。关掉之后所有桶都不限流（留给本地调试与压测）。"""
    return _env_int("VIDDIGEST_RATE_LIMIT_ENABLED", 1) != 0


def _window_seconds() -> int:
    """窗口长度（秒）。"""
    return max(1, _env_int("VIDDIGEST_RATE_LIMIT_WINDOW", 60))


def _limit_for(bucket: str) -> int:
    """某个桶的上限；0 表示不限流。"""
    env_name, default = BUCKETS[bucket]
    return _env_int(env_name, default)


def _is_trusted_peer(ip_text: str) -> bool:
    """直接对端是不是「可信代理」。

    回环与私网段算可信：本仓的 nginx 与后端在同一台机器/同一张 docker
    网络上，公网直连时对端是公网地址，不在可信段里——**不能**把
    `X-Forwarded-For` 无条件采信，否则限流形同虚设。

    为什么逐条列出网段，而不是用 `ipaddress` 的 `is_private`：
    Python 的 `is_private` 把**文档保留段**（192.0.2.0/24、198.51.100.0/24、
    203.0.113.0/24）也算成私有——那是给写文档用的地址段，真实部署里
    永远不会出现对端在那儿。用一个「看起来像私网、其实不是」的判断，
    等于把判据寄托在库实现的细节上。
    """
    try:
        ip = ip_address(ip_text)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETWORKS)


def _peer_ip(request: Request) -> str:
    """直接对端地址。"""
    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def client_identity(request: Request) -> str:
    """这个请求算「谁」发出来的。

    只有当直接对端可信时才采信 `X-Forwarded-For` 的第一个地址；
    否则用直接对端地址——宁可把同一 NAT 后面的人算成一个人，
    也不能让任何人改一个头就绕开限流。
    """
    peer = _peer_ip(request)
    if _is_trusted_peer(peer):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            try:
                ip_address(first)
            except ValueError:
                first = ""
            if first:
                return f"ip:{first}"
    return f"ip:{peer}"


def enforce(request: Request, bucket: str) -> None:
    """超了就抛 429；放行就什么都不做。"""
    if not _enabled():
        return
    limit = _limit_for(bucket)
    if limit <= 0:
        return
    allowed, _remaining, retry_after = _counter.hit(
        client_identity(request), limit, _window_seconds()
    )
    if allowed:
        return
    raise HTTPException(
        status_code=429,
        detail={
            "success": False,
            "error": f"请求过于频繁：服务器限流，请 {retry_after} 秒后再试",
        },
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit(bucket: str):
    """返回一个 FastAPI 依赖：挂在端点上，超限就 429。

    桶名写错要在**装配时**就炸（写错桶名等于端点没挂上限，还不报错）。
    """
    if bucket not in BUCKETS:
        raise KeyError(f"未知的限流桶: {bucket}")

    def dependency(request: Request) -> None:
        enforce(request, bucket)

    return dependency
