import os
import asyncio
import ipaddress
import re
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from downloader import VideoDownloader
from douyin import AUDIO_FORMAT_ID, DouyinParser, is_douyin_url
from database import init_db, seed_admin_emails_from_env

# 全局单例
downloader = VideoDownloader()
douyin_parser = DouyinParser(download_dir=downloader.DOWNLOAD_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库，关闭时清理下载文件"""
    init_db()
    # 播种必须在 init_db 之后：users 表还不存在时插不进去。
    # 未配置 VIDDIGEST_ADMIN_EMAILS 是合法配置，这里静默返回 0。
    seed_admin_emails_from_env()
    yield
    # 关闭时清理下载文件
    download_dir = downloader.DOWNLOAD_DIR
    if os.path.exists(download_dir):
        for f in os.listdir(download_dir):
            try:
                os.remove(os.path.join(download_dir, f))
            except OSError:
                pass


app = FastAPI(
    title="VidDigest API",
    description="基于 yt-dlp + LLM 的视频理解与下载服务，支持 1800+ 平台",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#: 请求模型里**绝不能出现在 422 校验错误体**里的字段名。
#:
#: FastAPI 的 422 默认把出错的 input 原样回显：
#: `{"loc": ["body", "user_api_key"], "input": ["sk-...", "x"]}`。
#: 校验失败的输入恰恰可能是用户刚发来的凭据（发成数组时尤其如此）。
#: 这**不是**跨边界泄漏——它只回给发请求的那个人自己——但工单 #9 的 AC4
#: 白纸黑字列了「异常响应体」，所以在这里抹掉，且只抹这一个字段。
_REDACTED_ON_VALIDATION_ERROR = {"user_api_key"}


@app.exception_handler(RequestValidationError)
async def _redact_credential_in_validation_error(request, exc):
    """422 响应里抹掉凭据字段的 input，其余结构与 FastAPI 默认一致。

    刻意不吞掉整个错误：loc / msg / type 照常返回，客户端仍能知道
    是哪个字段、为什么错。被遮蔽的只有 input——那是唯一可能含凭据的字段。
    """
    errors = []
    for err in exc.errors():
        err = dict(err)
        if any(part in _REDACTED_ON_VALIDATION_ERROR for part in err.get("loc", ())):
            err["input"] = "***"
        errors.append(err)
    return JSONResponse(status_code=422,
                        content={"detail": jsonable_encoder(errors)})


class ParseRequest(BaseModel):
    url: str

    def clean_url(self) -> str:
        """从输入文本中提取第一个有效 URL（兼容用户粘贴分享文本）"""
        match = re.search(r"https?://[^\s）\)\"\'＞，。、；：！？》>\]]+", self.url)
        return match.group(0) if match else self.url.strip()


class DownloadRequest(BaseModel):
    url: str
    format_id: str = "bestvideo+bestaudio/best"

    def clean_url(self) -> str:
        match = re.search(r"https?://[^\s）\)\"\'＞，。、；：！？》>\]]+", self.url)
        return match.group(0) if match else self.url.strip()


# ── 健康检查 ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ── 解析视频信息 ──────────────────────────────────────────

@app.post("/api/parse")
async def parse_video(req: ParseRequest):
    """解析视频信息（自动识别抖音/其他平台）"""
    try:
        url = req.clean_url()
        loop = asyncio.get_event_loop()
        if is_douyin_url(url):
            result = await loop.run_in_executor(None, douyin_parser.parse, url)
        else:
            result = await loop.run_in_executor(None, downloader.parse_video, url)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"解析失败: {str(e)}"
        })


# ── 服务端下载视频 ────────────────────────────────────────

@app.post("/api/download")
async def download_video(req: DownloadRequest):
    """服务端下载视频后提供文件下载（自动识别抖音/其他平台）"""
    try:
        url = req.clean_url()
        loop = asyncio.get_event_loop()
        if is_douyin_url(url):
            # 抖音这条路自己决定下什么，只认 mode 不认 format_id。不映射的话，
            # 选了「纯音频」会静默下回视频——界面上写着「下载音频」，
            # 而用户拿到的是一个带声的 mp4。
            mode = "audio" if req.format_id == AUDIO_FORMAT_ID else "video"
            result = await loop.run_in_executor(
                None, douyin_parser.download, url, mode
            )
        else:
            result = await loop.run_in_executor(
                None, downloader.download_video, url, req.format_id
            )
        filepath = result["filepath"]
        if not os.path.exists(filepath):
            raise HTTPException(status_code=500, detail="下载的文件不存在")

        return FileResponse(
            path=filepath,
            filename=result["filename"],
            media_type="application/octet-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"下载失败: {str(e)}"
        })


# ── 获取视频直链 ──────────────────────────────────────────

@app.post("/api/direct-url")
async def get_direct_url(req: DownloadRequest):
    """获取视频直链"""
    try:
        url = req.clean_url()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, downloader.get_direct_url, url, req.format_id
        )
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "success": False,
            "error": f"获取直链失败: {str(e)}"
        })


# ── 缩略图代理 ──────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    """读一个整数环境变量，取不到或不是数字时用默认值。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


#: 代理响应体上限（字节）。超了按 502 处理，不透传。
#:
#: 为什么要有：无上限意味着「任何人让服务器 GET 任意 URL 并把整块读进内存」，
#: 一次请求就能把内存吃满。与 15s 超时配套——超时管住慢，占内存这件事
#: 只能靠大小上限管。
#:
#: 读环境变量的形状照抄 `database._env_int`，但**不**从那里 import：
#: 那是它的私有助手，跨模块 import 私有名等于给它加一条谁都没审过的
#: 公开契约（工单 #38 立下的规矩）。
THUMBNAIL_MAX_BYTES = _env_int("VIDDIGEST_THUMBNAIL_MAX_BYTES", 8 * 1024 * 1024)


class _BadThumbnailTarget(Exception):
    """调用方给的地址**本身**就不该被代理（协议不对、没主机名）→ 400。"""


class _ThumbnailFetchFailed(Exception):
    """地址本身合规，但**取回来的东西**不能用（响应超限）→ 502。

    注意与 `_BadThumbnailTarget` 的分工：**地址**的问题归 400，
    **响应**的问题归 502。上游自己报的 4xx/5xx 走 httpx 的异常，也落 502。
    """


def _resolve_public_ips(host: str) -> list:
    """解析 host，返回**全部**都是公网地址的 IP 列表。

    判定**按解析结果**而不是按 URL 字面量：`http://evil.example/` 看着无害，
    解析出来是 127.0.0.1 就该拒。任何一个解析结果落在保留段就整体拒绝——
    「取第一个地址」会在多 A 记录的场景下漏掉后面那些。

    抛 ``_BadThumbnailTarget`` 表示「这个地址不该被代理」（→ 400）；
    域名压根解析不出来同样归这里：调用方给的就是一个无效地址。
    """
    try:
        addrinfos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise _BadThumbnailTarget(f"域名解析失败: {exc}") from exc

    ips = []
    for info in addrinfos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise _BadThumbnailTarget(f"不允许代理内网或保留地址: {ip_text}")
        ips.append(ip_text)

    if not ips:
        raise _BadThumbnailTarget(f"域名没有可用地址: {host}")
    return ips


@app.get("/api/proxy/thumbnail")
async def proxy_thumbnail(url: str = Query(..., description="缩略图URL")):
    """代理获取视频缩略图，绕过防盗链。

    **刻意不挂鉴权依赖**：社区卡片要显示缩略图，公开可读是对的。要收紧的
    是「能代理哪些地址」，不是「谁能代理」。

    收紧了什么（工单 #46）：原实现对 ``url`` 零校验，于是任何人可让本服务
    GET 任意 URL 并把响应体原样吐回来——内网地址（含云环境的
    ``169.254.169.254`` 元数据服务）都在射程内。现在按**解析后的 IP** 判，
    且请求 URL 里是**已判定的那个 IP**（不再解析域名，DNS rebinding 窗口关闭），
    而 **Host 与 SNI 仍是原域名**——CDN 按虚拟主机路由，这两者换成 IP 会
    全部握手失败。详见下面那两行的实测记录。
    """
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise _BadThumbnailTarget("只支持 http/https")
        host = parts.hostname
        if not host:
            raise _BadThumbnailTarget("URL 缺少主机名")

        ips = _resolve_public_ips(host)
        target = f"{parts.scheme}://{ips[0]}{parts.path or '/'}"
        origin = parts.netloc.rsplit("@", 1)[-1]  # 去掉 userinfo

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                target,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": url,
                    # **Host 与 SNI 都必须是原域名**，只有连接目标换成已判定的 IP。
                    #
                    # 为什么不能图省事把 Host 也写成 IP（第一版就这么写的）：
                    # 实测两个真实 HTTPS 图床**全部握手失败**——
                    #   www.bilibili.com → CERTIFICATE_VERIFY_FAILED: IP address mismatch
                    #   example.com      → SSLV3_ALERT_HANDSHAKE_FAILURE
                    # 因为 CDN 按虚拟主机路由，拿到 IP 形态的 SNI 既拿不到对的证书
                    # 也选不对后端。那等于把社区卡片的所有 HTTPS 缩略图**全打坏**。
                    #
                    # rebinding 防线仍然在：URL 里是 IP，httpx **不会再去解析域名**，
                    # 连的就是刚判定过的那个地址；域名只用于 TLS 握手与 Host 头。
                    "Host": origin,
                },
                # sni_hostname 是 httpcore 的扩展项（httpx 的 get/build_request 都收）。
                # 不给的话 TLS 握手的 SNI 会取 URL 的 host——也就是那个 IP。
                extensions={"sni_hostname": host},
            )
            resp.raise_for_status()
            if len(resp.content) > THUMBNAIL_MAX_BYTES:
                raise _ThumbnailFetchFailed("缩略图超过大小上限")
            content_type = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                iter([resp.content]),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except _BadThumbnailTarget as e:
        # 调用方给的地址本身就不该被代理 —— 400，比 502 准。
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="缩略图加载失败")


# 挂载功能模块
from api_summarize import router as summarize_router  # noqa: E402
from api_auth import router as auth_router  # noqa: E402
from api_payment import router as payment_router  # noqa: E402
from api_history import router as history_router  # noqa: E402
from api_community import router as community_router  # noqa: E402
from admin_api import router as admin_router  # noqa: E402
app.include_router(summarize_router)
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(history_router)
app.include_router(community_router)
app.include_router(admin_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
