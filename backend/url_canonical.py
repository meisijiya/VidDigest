"""视频链接的**查询侧**归一（工单 #25 第一步）。

## 它解决什么

同一个视频，用户完全正常地会粘到好几种形态：无 `www`、移动域名、
跟踪参数、尾斜杠、从 App 分享的整段文案。而 `videos.video_url` 上的唯一索引
（工单 #6 定下的「同一链接全站只解析一次」）做的是**精确比较**，
于是这些形态各自占一行 —— 解析过一次之后从另一种形态打开，
「社区里已有结果」判不出来，自动展示不触发。

## 它刻意**不**解决什么

- **短链**：`b23.tv` / `xhslink.com` 的真实视频 id 根本不在 URL 里，
  必须先发一次网络请求解析跳转才知道自己是谁。那是产品行为变更
  （拒绝）或新增出网依赖，**不在本工单**，留作独立立项。
  未识别的链接一律**原样返回**，绝不做「猜一个规范值」的改写 ——
  改错了会让两个不同的视频在库里塌成一行。
- **爱奇艺的 `.html` 后缀**：不带 `.html` 不掉进 iqiyi extractor、
  掉进 `[generic]` 兜底（AGENTS.md 实测）。所以这里的规则**保留** `.html`，
  不去「修」它 —— 那属于下载侧的取舍，不是去重的取舍。

## `youtu.be` 为什么算在内

严格说它是短链，但**视频 id 就在路径里**（`youtu.be/dQw4w9WgXcQ`），
映射到 `youtube.com/watch?v=…` 是一次纯字符串替换，不需要出网。
把它排除在外只会白留一个「同一个 YouTube 视频两行」的洞。

## 不变量

1. 归一**幂等**：`canonical(canonical(u)) == canonical(u)`。
2. **原样返回的分支必须真的原样返回**：不能 trim、不能补尾斜杠。
   这条是防「顺手统一一下」的那只手——统一是去重逻辑的活，
   不是这里该做的。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# ── 平台规则表 ───────────────────────────────────────────────
#
# 形态全部来自工单 #25 的实测矩阵（同一批构造 URL 跑真实比较），
# 不来自印象。新增平台前先把形态矩阵补上，别凭「大概是这个形状」写规则。

#: B 站视频号。BV 号固定 12 位、BV1 开头 10 位；这里不写死长度，
#: 因为 yt-dlp 接受什么就该归一什么 —— 规则只负责**取出**它。
#:
#: ⚠️ **必须锚定结尾**（`$`）。这一条不是洁癖，是实测出来的漏洞：
#: 不锚定时 `/video/BV1TEST/a` 与 `/video/BV1TEST/b` 都会被截成 `BV1TEST`，
#: 于是两个**不同的**视频塌成同一个 canonical —— 而那种失效不报错，
#: 只是社区慢慢塌。第 3 片建上唯一索引之后，它会立刻变成「后写的那个
#: 被当成同一个视频而合并掉」，实测由 `test_history_search_favorites.py`
#: 的 `test_facets_are_not_narrowed_by_the_current_filters` 抓到。
_BILIBILI_VIDEO = re.compile(r"^/video/(?P<id>BV[0-9A-Za-z]+)$", re.I)
_YOUTUBE_WATCH = re.compile(r"^/watch$", re.I)
#: 抖音同理要锚定：`/video/123/a` 不是一条视频。
_DOUYIN_VIDEO = re.compile(r"^/(?:share/)?video/(?P<id>\d+)$", re.I)

#: 小红书笔记路径。
#:
#: ⚠️ 这条**只用来判形态，不用来重写 id**：小红书的 `?xsec_token=…` 是平台的
#: 反爬参数，抹掉会让本来能解析的链接解析不了（与 B 站不同——B 站的
#: `spm_id_from` 之类确实是跟踪参数）。所以小红书这条规则**只换 host**，
#: path 与 query 一律原样带过去。
#:
#: 同样必须锚定结尾：`/explore/<id>/anything` 不是一条笔记。
_XIAOHONGSHU_NOTE = re.compile(
    r"^/(?:explore|discovery/item)/(?P<id>[0-9a-f]+)$", re.I
)

#: 这些 host 归一到哪一个（大小写无关）。B 站的三个域名指向同一份内容。
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
_XIAOHONGSHU_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com", "m.xiaohongshu.com"}


def _strip(url: str) -> tuple[str, str, str]:
    """拆成 (host小写, path, )，顺手去掉尾斜杠与 query/fragment。

    尾斜杠单独处理：`/video/BVxxx/` 与 `/video/BVxxx` 是同一条视频，
    而 query / fragment 由 urlsplit 直接丢掉——**包括末尾一个裸 `?`**
    （`clean_url()` 从分享文案里抽出来的就是这个形态）。
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return host, path, parts.query


def canonical_video_url(url: str) -> str:
    """返回这个链接的**规范形态**。未识别的平台原样返回。

    >>> canonical_video_url("https://www.bilibili.com/video/BV1aa411c7mD/?spm_id_from=333.1007.tianma#reply")
    'https://www.bilibili.com/video/BV1aa411c7mD'
    >>> canonical_video_url("https://youtu.be/dQw4w9WgXcQ?t=42")
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    >>> canonical_video_url("https://v.example/x?sig=SECRET")
    'https://v.example/x?sig=SECRET'
    """
    if not url:
        return url

    host, path, query = _strip(url)

    # ── B 站 ──
    if host in _BILIBILI_HOSTS:
        m = _BILIBILI_VIDEO.match(path)
        if m:
            return f"https://www.bilibili.com/video/{m.group('id')}"
        # 其余一律原样返回：`/list/` 是合集页（id 不唯一，塌成一行会毁掉整个合集），
        # `/video/<id>/<别的>` 也不是一条视频（见上面那条正则的注释）。
        return url

    # ── YouTube：watch?v=<id> 与 youtu.be/<id> 是同一条 ──
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if _YOUTUBE_WATCH.match(path):
            for pair in query.split("&"):
                key, _, value = pair.partition("=")
                if key == "v" and value:
                    return f"https://www.youtube.com/watch?v={value}"
        return url
    if host == "youtu.be":
        vid = path.lstrip("/")
        if vid and "/" not in vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return url

    # ── 抖音 ──
    if host in _DOUYIN_HOSTS:
        m = _DOUYIN_VIDEO.match(path)
        if m:
            return f"https://www.douyin.com/video/{m.group('id')}"
        return url

    # ── 小红书：只换 host，path 与 query 一律原样带过去 ──
    #
    # 与上面三个平台的差别在这里：它们的 query 是跟踪参数，可以丢；
    # 小红书的 `xsec_token` 是反爬参数，**丢了就解析不了**。
    if host in _XIAOHONGSHU_HOSTS:
        if _XIAOHONGSHU_NOTE.match(path):
            tail = f"{path}?{query}" if query else path
            return f"https://www.xiaohongshu.com{tail}"
        # `/user/profile/` 是主页不是笔记，原样返回（归一了也一样解析不了，
        # 但那是平台的事，不该由这里改写用户的地址）
        return url

    # ── 其余一律原样返回（短链、爱奇艺、以及任何不认识的主机）──
    return url


def url_for_ytdlp(url: str) -> str:
    """**交给 yt-dlp 之前**把链接归一成本仓认识的形态。

    与 `canonical_video_url` 结果相同，但名字不同是为了让调用点自己说明
    「我改的是喂给 yt-dlp 的那个字符串」。

    ## 只能用在这里

    ⚠️ **不要拿它去写库或回显。** ADR 0016 定的是「比较用规范值、回显用原文」。
    `SummarizeRequest.url` 同时喂给 `reserve_video` 等数据库写入，
    在入口处替换会把 canonical 写进 `video_url`，
    于是社区卡片回显的变成规范值——**静默推翻那个决定**，
    而且现有测试一条都不会红（它们读的是同一个变量）。

    未识别的主机上本函数**逐字返回原文**，所以对绝大多数链接零行为变化。
    """
    return canonical_video_url(url)