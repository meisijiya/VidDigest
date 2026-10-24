"""端到端自检：伪造 X-Forwarded-For 不得绕开限流（工单 #94、ADR 0022）。

**为什么要有这条**：限流的「来源身份」是**边缘代理说了算**的——后端能看到的
只有 `X-Forwarded-For`，而这个头是客户端可以随便写的。边缘代理若用**追加**
语义（nginx 的 `$proxy_add_x_forwarded_for`），客户端自带的值就排在最前，
后端取到的就是攻击者自报的地址 ⇒ 每次换一个头就换一个桶，限流形同虚设。

**这条判据必须打真的代理**：只测后端函数测不到「代理把客户端伪造的头原样
透传」这一环——而那一环正是缺陷的一半。所以它打的是**跑着的 nginx**
（默认 http://127.0.0.1:8080）。

**怎么算通过**：连打 N 次（默认 25）请求，每次带一个**唯一**的伪造
`X-Forwarded-For`，必须**至少出现一次 429**，且该 429 的响应体里
`detail.code` 是 `rate_limit`。一次 429 都没有 ⇒ 限流被绕过了 ⇒ 退出码 1。

用法：
    python scripts/ops/check_xff_bypass.py
    python scripts/ops/check_xff_bypass.py --url http://127.0.0.1:8080/api/parse --requests 25

退出码：0 = 限流挡住了伪造身份；1 = 一次 429 都没有（可被绕过）；
2 = 参数写错或端点连不上（那不是「通过」，那是「没测到」）。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8080/api/parse"
DEFAULT_REQUESTS = 25
#: 伪造来源用 RFC 5737 的文档保留段：真实公网上不可路由，打不到任何真人。
FORGED_PREFIX = "203.0.113."
#: 解析必定失败的地址：`.invalid` 是 RFC 2606 保留给「一定解析不了」的顶级域。
#: 这样的请求会回 400——那与限流无关，我们只看有没有 429。
BODY = {"url": "https://example.invalid/x"}
TIMEOUT = 30


def _post(url, forged):
    """发一次请求，返回 (状态码, 响应体字节)。

    429 也要拿到 body：判据不只看状态码，还看错误体里的 `detail.code`。
    """
    req = urllib.request.Request(
        url,
        data=json.dumps(BODY).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Forwarded-For": forged,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _is_rate_limit(status, payload):
    """这个 429 是不是「速率闸门」放出来的（而不是并发闸门或别的东西）。"""
    if status != 429:
        return False
    try:
        body = json.loads(payload.decode("utf-8", "replace"))
        detail = body.get("detail") or {}
    except (ValueError, AttributeError):
        return False
    return detail.get("code") == "rate_limit"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="自检：伪造 X-Forwarded-For 不得绕开限流",
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help="要打的端点（默认经 nginx 的 :8080；直连后端 :8000 不算这条守卫）",
    )
    parser.add_argument(
        "--requests", type=int, default=DEFAULT_REQUESTS,
        help="打多少次（默认 25；要超过该端点的窗口上限才看得到 429）",
    )
    args = parser.parse_args(argv)
    if args.requests < 1:
        print("参数写错：--requests 至少为 1")
        return 2

    statuses = {}
    hits = 0
    for i in range(1, args.requests + 1):
        forged = "%s%d" % (FORGED_PREFIX, i)
        try:
            status, payload = _post(args.url, forged)
        except (urllib.error.URLError, OSError) as exc:
            print("打第 %d 次时连不上 %s：%s" % (i, args.url, exc))
            print("这不算通过——没打到服务就等于没测到。")
            return 2
        statuses[status] = statuses.get(status, 0) + 1
        if _is_rate_limit(status, payload):
            hits += 1

    summary = "、".join("%d × %d" % (code, n) for code, n in sorted(statuses.items()))
    print("打了 %d 次（每次一个唯一的伪造 X-Forwarded-For），状态码分布：%s"
          % (args.requests, summary))
    print("命中 429（detail.code == rate_limit）：%d 次" % hits)
    if hits == 0:
        print("FAIL：一次 429 都没有 —— 伪造 X-Forwarded-For 绕开了限流。")
        print("查两处：边缘代理有没有**覆盖** X-Forwarded-For"
              "（frontend/nginx.conf 的 location /api/ 块），"
              "后端取的是不是**末段**（backend/ratelimit.py 的 client_identity）。")
        return 1
    print("OK：伪造的 X-Forwarded-For 没有绕开限流。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
