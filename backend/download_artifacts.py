"""下载产物的清理口径（工单 #90）。

两条下载通道（通用 yt-dlp 与抖音）把文件落进**同一个目录**，
ASR 通道也往同一个目录里写临时音频。于是「文件用完就删」这件事
只能有一份实现：散在两条通道里，两边会慢慢长出差异，而差异的表现是
「某个平台的残留永远清不掉」——事后很难看出是哪一边漏了。

这里提供两件事：

1. `remove_artifact()`：删掉一个**已知路径**的产物。删不掉不算失败——
   响应这时已经发出去了，因为清理失败把一次成功的下载变成一次失败的
   下载，是拿小错换大错。
2. `cleanup_partial_artifacts()`：清掉下载中断留下的半截文件。
   抖音那条通道自己知道半截文件叫什么名字，由它自己清（见
   `backend/douyin.py`）；这里管的是 yt-dlp 留下的 `.part` / `.ytdl`。

**为什么不按「目录里所有新文件都删」来清理**：下载目录是共享的，
别的请求正在往里写。把整个目录扫一遍清掉，会删掉别人的在途文件——
那是把一个「残留占地方」的问题，换成一个「下载坏掉」的问题。
宁可漏清：漏掉的半截文件巡检会按超龄收走
（`scripts/ops/inspect_runtime.py` 的第 6 项）。
"""
import logging
import os

logger = logging.getLogger("download_artifacts")

#: 下载中断时留下的文件名后缀。yt-dlp 先写 `<名字>.part`，写完才改名；
#: `.ytdl` 是它的断点续传状态文件。
PARTIAL_SUFFIXES = (".part", ".ytdl")

#: 时间戳比文件系统时间戳粒度还小一拍的余量（秒）。
#:
#: 有些文件系统的时间戳只精确到秒，文件写完的 mtime 可能比请求开始的
#: 时刻还早一点点。没有这个余量，刚写下的半截文件会被漏掉。
TS_TOLERANCE_SECONDS = 1.0


def is_partial_artifact(name: str) -> bool:
    """这个文件名看起来是不是下载中断留下的半截文件。"""
    return name.endswith(PARTIAL_SUFFIXES)


def remove_artifact(path) -> bool:
    """删掉一个下载产物，返回是否真的删掉了。

    文件本来就不在也算成功（幂等）：清理可能被跑两遍，也可能在别的
    进程已经删过之后才轮到我们。
    """
    if not path:
        return False
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        # 清理失败只记日志，不往上抛：调用点通常在响应发出之后。
        logger.warning("清理下载产物失败 %s: %s", path, exc)
        return False


def cleanup_partial_artifacts(download_dir, since_ts) -> list:
    """清掉 `download_dir` 里在 `since_ts` 之后才出现的半截文件。

    `since_ts` 是这次请求开始的时刻。判定只认「请求开始之后出现的
    半截文件」，这样别的请求正在写的在途文件不会被误删。

    返回被删掉的文件路径列表（删不掉的不算删掉了）。
    """
    if not download_dir or not os.path.isdir(download_dir):
        return []
    cutoff = since_ts - TS_TOLERANCE_SECONDS
    removed = []
    try:
        names = os.listdir(download_dir)
    except OSError as exc:
        logger.warning("列下载目录失败 %s: %s", download_dir, exc)
        return []
    for name in names:
        if not is_partial_artifact(name):
            continue
        full = os.path.join(download_dir, name)
        try:
            mtime = os.path.getmtime(full)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        if remove_artifact(full):
            removed.append(full)
    return removed
