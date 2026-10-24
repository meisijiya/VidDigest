"""下载产物的清理口径（工单 #90）。

两条下载通道（通用 yt-dlp 与抖音）把文件落进**同一个目录**，
ASR 通道也往同一个目录里写临时音频。于是「文件用完就删」这件事
只能有一份实现：散在两条通道里，两边会慢慢长出差异，而差异的表现是
「某个平台的残留永远清不掉」——事后很难看出是哪一边漏了。

这里提供四件事：

1. `file_identity()`：记下一个文件此刻的身份。产物名不带唯一 id，
   同一个标题会落到同一个路径上，清理时得先确认盘上这个文件还是
   我们交出去的那一个。
2. `remove_artifact()`：删掉一个**已知路径**的产物。删不掉不算失败——
   响应这时已经发出去了，因为清理失败把一次成功的下载变成一次失败的
   下载，是拿小错换大错。
3. `WrittenPaths`：一次下载自己写下了哪些文件（yt-dlp 的进度钩子报出来
   的），下载失败时按这份清单清，不用去扫目录。
4. `ArtifactResponse`：把「用完即删」挂在响应自己的 `finally` 上。

**为什么清理只认「本次下载自己写下的路径」**：下载目录是共享的，
别的请求正在往里写。按「目录里所有新文件都删」或「按时间窗口扫目录」
来清理，会删掉别人的在途文件——那是把一个「残留占地方」的问题，
换成一个「下载坏掉」的问题。宁可漏清：漏掉的半截文件巡检会按超龄收走
（`scripts/ops/inspect_runtime.py` 的第 6 项）。
"""
import logging
import os

from fastapi.responses import FileResponse

logger = logging.getLogger("download_artifacts")


def file_identity(path):
    """记下一个文件此刻的身份，返回 `(st_dev, st_ino, st_size, st_mtime_ns)`。

    为什么需要身份：产物名不带唯一 id，同一个标题会落到同一个路径上。
    两个请求下同一个视频，第二个会把第一个的产物覆盖掉；清理时如果还
    按「这个路径就是我的」去删，删掉的就是别人的东西。

    为什么是这四个字段：Windows 与 Linux 上都稳——Windows 的 `st_ino`
    是文件索引（不是 0），`st_dev` 是卷号；`st_size` 与 `st_mtime_ns`
    补上「同一个文件被改写」这种情况。文件不在时返回 None。
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def remove_artifact(path, identity=None) -> bool:
    """删掉一个下载产物，返回是否真的删掉了。

    文件本来就不在也算成功（幂等）：清理可能被跑两遍，也可能在别的
    进程已经删过之后才轮到我们。

    `identity` 是这个文件在交给客户端**之前**的身份（见 `file_identity()`）。
    给了它就意味着「只删这一个文件」：盘上的文件已经不是它了就跳过——
    宁可漏清，也不删掉别人的产物。
    """
    if not path:
        return False
    if identity is not None and file_identity(path) != identity:
        logger.info("跳过清理 %s：盘上的文件已经不是交出去的那一个了", path)
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


class WrittenPaths:
    """一次下载自己写下的文件清单。

    yt-dlp 通过 `progress_hooks` 报出它写到哪儿了：下载中报的是**正在
    写**的那个临时文件（`<名字>.part`），完成时报的是**刚写完**的最终
    文件。下载失败时按这份清单清，不用去扫目录——扫目录会删掉别的请求
    正在写的在途文件。
    """

    def __init__(self):
        self._paths = set()

    def track_progress(self, status: dict) -> None:
        """yt-dlp 的下载进度钩子。

        「下载中」的 `tmpfilename` 是它正在写的那个文件，此刻最终名可能还
        属于别人（同名视频会落到同一个路径），所以只记临时文件；「完成」
        意味着那个最终文件刚由本次下载改名而来，可以当成「本次的产物」。
        """
        if status.get("status") == "finished":
            final = status.get("filename")
            if final:
                self._paths.add(final)
                # `.ytdl` 是断点续传的状态文件，跟在最终文件名后面。
                self._paths.add(final + ".ytdl")
            return
        tmp = status.get("tmpfilename")
        if tmp:
            self._paths.add(tmp)

    def remove_all(self) -> list:
        """把这次下载写下的文件都删掉，返回真删掉了的路径。

        删不掉的只记日志：这是失败路径上的收尾，往上抛会把真正的失败
        原因盖掉。
        """
        removed = []
        for path in sorted(self._paths):
            if remove_artifact(path):
                removed.append(path)
        return removed


class ArtifactResponse(FileResponse):
    """把「用完即删」挂在响应自己的 `finally` 上。

    为什么不用框架注入的后台任务（`background_tasks.add_task(...)`）：
    starlette 的 `FileResponse.__call__` 末尾那句 `await self.background()`
    **不在 try/finally 里**——客户端中途断开时那一行根本不会执行，
    产物就永久留在盘上了。挂在 `finally` 上，正常发完与中途断开都会删。

    清理仍然在响应**发完之后**：响应体就是从这个文件里流出去的，
    先删就变成 0 字节。
    """

    def __init__(self, *args, identity, **kwargs):
        super().__init__(*args, **kwargs)
        self._identity = identity

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # 删不掉只记日志，不影响响应：这时响应要么已经发出去了，
            # 要么正在失败中，把清理的失败变成用户看到的错误是拿小错换大错。
            remove_artifact(self.path, self._identity)
