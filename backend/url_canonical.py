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
_BILIBILI_VIDEO = re.compile(r"^/(?:video|list)/(?P<id>BV[0-9A-Za-z]+)", re.I)
_YOUTUBE_WATCH = re.compile(r"^/watch$", re.I)
_DOUYIN_VIDEO = re.compile(r"^(?:/video|/(?:share/)?video)/(?P<id>\d+)", re.I)

#: 这些 host 归一到哪一个（大小写无关）。B 站的三个域名指向同一份内容。
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
_DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}


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
        # /list/ 是合集页不是单条视频，id 不唯一 —— 交给原样返回。
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

    # ── 其余一律原样返回（短链、爱奇艺、小红书、以及任何不认识的主机）──
    return url