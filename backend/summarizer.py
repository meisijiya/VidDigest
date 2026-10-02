"""字幕提取与 AI 视频总结模块"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import yt_dlp
from openai import OpenAI

from credentials import UserCredential
from tags import vocabulary_prompt_text

logger = logging.getLogger("summarizer")

# ── extract() 的失败原因 ────────────────────────────────
# 落穿到 ASR 曾经是静默的：一条明明带平台字幕的视频，只要字幕下载失败，
# 就悄悄去烧一次语音识别，调用方还分不清「本来就没有字幕」和「本来有却没拿到」。
# 下面这组取值让这两种结局在返回值里就分得开。
FAIL_NO_TRACK = "no_subtitle_track"            # 平台没给任何字幕轨道
FAIL_FETCH_FAILED = "subtitle_fetch_failed"    # 有字幕轨道，但下载/解析拿不到内容
FAIL_ASR_NOT_CONFIGURED = "asr_not_configured"  # 落穿到 ASR，但没配 OPENAI_API_KEY
FAIL_ASR_FAILED = "asr_failed"                # 落穿到 ASR，但下载音频或转写失败


def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径。

    只用 shutil.which + PATH 扫描，不再硬编码任何人的用户目录，
    也不通过 `ffmpeg -version` 子进程探测（探测是 import 阶段的副作用来源）。
    """
    found = shutil.which("ffmpeg")
    if found:
        return found

    # PATH 里带引号或相对项时 which 可能漏掉，补一次直接扫描
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not path_dir:
            continue
        for name in ("ffmpeg.exe", "ffmpeg"):
            full = os.path.join(path_dir, name)
            if os.path.isfile(full):
                return full

    return "ffmpeg"  # 兜底：交给调用方报错


def _is_bilibili_url(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


def _is_douyin_url(url: str) -> bool:
    douyin_domains = ["douyin.com", "iesdouyin.com", "v.douyin.com"]
    from urllib.parse import urlparse
    try:
        host = urlparse(url).netloc.lower()
        return any(d in host for d in douyin_domains)
    except Exception:
        return False


def _time_to_seconds(ts: str) -> float:
    """将 HH:MM:SS.mmm 格式转换为秒数"""
    parts = ts.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


class SubtitleExtractor:
    """从视频 URL 提取平台字幕（人工字幕 > 自动字幕）"""

    PREFERRED_LANGS = ["zh-Hans", "zh", "zh-CN", "en", "ja", "ko"]
    SUBTITLE_FORMAT = "json3"

    def extract(self, url: str) -> dict:
        """提取视频字幕，优先下载平台字幕，失败则回退到语音转写。

        落穿到 ASR 是显式决策：``fail_reason`` 记平台字幕为什么没拿到
        （没有轨道 / 有轨道但拿不到），``asr_fail_reason`` 记兜底那一步为什么
        没救回来（未配置 / 转写失败）。两个字段成功时都是空串。
        """
        # 1. B 站专用 API
        if _is_bilibili_url(url):
            result = self._extract_bilibili(url)
            if result["has_subtitle"]:
                return result

        # 2. 尝试 yt-dlp 下载字幕
        #    track_seen 记的是「平台到底给没给字幕轨道」——它决定落穿时报哪种原因
        track_seen = False
        try:
            info = self._get_video_info(url)
            manual_subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}
            manual_subs = {k: v for k, v in manual_subs.items() if k != "danmaku"}

            if manual_subs or auto_subs:
                track_seen = True
                lang, sub_url, sub_type = self._pick_best_subtitle(manual_subs, auto_subs)
                if sub_url:
                    segments = self._download_subtitle_json(sub_url)
                else:
                    segments = self._download_and_parse_via_ytdlp(url, lang, sub_type)

                if segments:
                    full_text = self._join_segments(segments, lang)
                    return {
                        "has_subtitle": True,
                        "language": lang,
                        "subtitle_type": sub_type,
                        "segments": segments,
                        "full_text": full_text,
                        "fail_reason": "",
                        "asr_fail_reason": "",
                    }
                # 轨道存在却拿不到内容：显式记账，不与「没有轨道」混成一谈
                logger.warning(
                    "字幕轨道存在（%s / %s）但下载或解析为空",
                    sub_type or "未知类型", lang or "未知语言",
                )
        except Exception as e:
            logger.warning("yt-dlp subtitle extraction failed: %s, trying ASR fallback", e)

        # 3. ASR 语音转写回退
        fallback_reason = FAIL_FETCH_FAILED if track_seen else FAIL_NO_TRACK
        logger.info("平台字幕不可用（%s），转写语音作为兜底", fallback_reason)
        result = _transcribe_audio(url)
        # fail_reason 只在真的没拿到字幕时有值；ASR 兜底成功了就不该带失败原因
        if not result["has_subtitle"]:
            # 平台侧的原因优先：用户真正要问的是「这条视频为什么没有字幕」
            result["fail_reason"] = fallback_reason
        return result

    # ── B 站专用字幕提取 ────────────────────────────────

    @staticmethod
    def _parse_bvid(url: str) -> Optional[str]:
        """从 B 站 URL 中提取 BV 号"""
        patterns = [
            r"BV\w{10,12}",
            r"bvid=([^&]+)",
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(0) if m.group(0).startswith("BV") else m.group(1)
        return None

    @staticmethod
    def _join_segments(segments: list[dict], lang: str = "") -> str:
        """按语言选择连接符：中文用空串（保证语句连贯），其他语言用空格"""
        sep = "" if (lang or "").lower().startswith("zh") else " "
        return sep.join(seg["text"] for seg in segments)

    def _extract_bilibili(self, url: str) -> dict:
        """B 站专用字幕提取"""
        empty = {
            "has_subtitle": False, "language": "",
            "subtitle_type": "none", "segments": [], "full_text": "",
            "fail_reason": "", "asr_fail_reason": "",
        }
        try:
            bvid = self._parse_bvid(url)
            if not bvid:
                return empty

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": f"https://www.bilibili.com/video/{bvid}",
            }

            # 1. 获取视频信息（含 cid 和 aid）
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                view_resp = client.get(
                    f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                    headers=headers,
                )
                view_resp.raise_for_status()
                view_data = view_resp.json().get("data", {})
                cid = view_data.get("cid")
                aid = view_data.get("aid")
                if not cid or not aid:
                    return empty

                # 2. 获取字幕列表
                dm_resp = client.get(
                    f"https://api.bilibili.com/x/v2/dm/view?aid={aid}&oid={cid}&type=1",
                    headers=headers,
                )
                dm_resp.raise_for_status()
                dm_data = dm_resp.json().get("data", {})
                subtitle_list = dm_data.get("subtitle", {}).get("subtitles", [])

                if not subtitle_list:
                    return empty

                # 3. 选择最佳字幕：优先任何中文轨道（zh / zh-Hans / ai-zh），
                #    避免误选英文翻译轨道（ai-en）
                def _lang(s: dict) -> str:
                    return (s.get("lan") or "").lower()

                best = None
                for s in subtitle_list:  # 第一优先：中文轨道
                    if _lang(s).startswith("zh"):
                        best = s
                        break
                if best is None:  # 第二优先：非英语轨道
                    for s in subtitle_list:
                        if not _lang(s).startswith("en"):
                            best = s
                            break
                if best is None:  # 兜底：列表第一个
                    best = subtitle_list[0]

                sub_type = "auto" if best.get("lan", "").startswith("ai-") else "manual"
                sub_url = best.get("subtitle_url", "")
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url

                # 4. 下载字幕 JSON
                sub_resp = client.get(sub_url, headers=headers)
                sub_resp.raise_for_status()
                sub_json = sub_resp.json()
                body = sub_json.get("body", [])

                segments = []
                for item in body:
                    content = item.get("content", "").strip()
                    if not content:
                        continue
                    segments.append({
                        "start": round(item.get("from", 0), 2),
                        "end": round(item.get("to", 0), 2),
                        "text": content,
                    })

                full_text = self._join_segments(segments, best.get("lan", ""))
                return {
                    "has_subtitle": True,
                    "language": best.get("lan", "zh"),
                    "subtitle_type": sub_type,
                    "segments": segments,
                    "full_text": full_text,
                    "fail_reason": "",
                    "asr_fail_reason": "",
                }
        except Exception:
            return empty

    # ── 通用字幕提取（非 B 站平台）────────────────────

    @staticmethod
    def _get_video_info(url: str) -> dict:
        """通过 yt-dlp 获取视频元数据（不下载）"""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    @staticmethod
    def _pick_best_subtitle(manual_subs: dict, auto_subs: dict) -> tuple:
        """从字幕列表中选择最佳字幕，返回 (lang, url, type)"""
        preferred = ["zh-Hans", "zh", "zh-CN", "ai-zh", "en", "ja", "ko"]

        def _sorted_keys(subs: dict) -> list:
            """在偏好列表之外，中文轨道（zh*）优先于其他语言"""
            rest = [k for k in subs if k not in preferred]
            return preferred + sorted(rest, key=lambda k: (not k.startswith("zh"), k))

        def _pick_from(subs: dict, sub_type: str) -> tuple:
            for lang in _sorted_keys(subs):
                entry = subs.get(lang)
                if entry:
                    for fmt in entry:
                        if fmt.get("ext") == "json3":
                            return lang, fmt["url"], sub_type
                    for fmt in entry:
                        return lang, fmt["url"], sub_type
            return ("", "", "")

        # 优先人工字幕，其次自动字幕
        result = _pick_from(manual_subs, "manual")
        if result[1]:
            return result
        return _pick_from(auto_subs, "auto")

    @staticmethod
    def _download_subtitle_json(sub_url: str) -> list:
        """从 URL 下载字幕 JSON 并解析为分段列表"""
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(sub_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        if isinstance(data, dict):
            body = data.get("body", data.get("events", []))
        elif isinstance(data, list):
            body = data
        else:
            return []

        segments = []
        for item in body:
            text = ""
            start = 0
            end = 0
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                start = item.get("start") or item.get("from") or 0
                end = item.get("end") or item.get("to") or 0
                segs = item.get("segs")
                if not text and segs:
                    text = " ".join(s.get("utf8", "") for s in segs if s.get("utf8"))
            elif isinstance(item, str):
                text = item

            text = text.strip()
            if text:
                segments.append({
                    "start": round(float(start), 2),
                    "end": round(float(end), 2),
                    "text": text,
                })

        return segments

    def _download_and_parse_via_ytdlp(self, url: str, lang: str, sub_type: str) -> list:
        """通过 yt-dlp 下载字幕文件后解析"""
        import os
        with tempfile.TemporaryDirectory() as tmp_dir:
            ydl_opts = {
                "quiet": True, "no_warnings": True, "noplaylist": True,
                "skip_download": True,
                "writesubtitles": sub_type == "manual",
                "writeautomaticsub": sub_type == "auto",
                "subtitleslangs": [lang],
                "subtitlesformat": "vtt",
                "outtmpl": os.path.join(tmp_dir, "subtitle"),
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception:
                return []

            vtt_files = [f for f in os.listdir(tmp_dir) if f.endswith(".vtt")]
            if not vtt_files:
                return []

            return self._parse_vtt(os.path.join(tmp_dir, vtt_files[0]))

    @staticmethod
    def _parse_vtt(filepath: str) -> list[dict]:
        """解析 VTT 字幕文件为结构化分段"""
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        segments = []
        blocks = re.split(r"\n\n+", content)
        time_pattern = re.compile(
            r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
        )

        seen_texts = set()
        for block in blocks:
            lines = block.strip().split("\n")
            time_match = None
            text_lines = []
            for line in lines:
                m = time_pattern.search(line)
                if m:
                    time_match = m
                elif time_match and line.strip() and not line.strip().isdigit():
                    clean = re.sub(r"<[^>]+>", "", line.strip())
                    if clean:
                        text_lines.append(clean)

            if time_match and text_lines:
                text = " ".join(text_lines)
                if text in seen_texts:
                    continue
                seen_texts.add(text)
                segments.append({
                    "start": _time_to_seconds(time_match.group(1)),
                    "end": _time_to_seconds(time_match.group(2)),
                    "text": text,
                })

        return segments


# ── ASR 语音转写（回退方案 - Whisper API）──────────────

_FFMPEG_PATH: Optional[str] = None


def _ffmpeg_path() -> str:
    """惰性解析并缓存 ffmpeg 路径。

    原实现在 import 阶段就执行 `ffmpeg -version`，让任何 import 本模块的
    脚本、测试或工具都无条件付出一次子进程探测的代价。
    """
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        _FFMPEG_PATH = _find_ffmpeg()
        logger.debug("ffmpeg resolved to %s", _FFMPEG_PATH)
    return _FFMPEG_PATH


def _transcribe_audio(url: str) -> dict:
    """下载音频并使用 OpenAI Whisper API 转写为文字。

    凭据只有 ``OPENAI_API_KEY`` 一个来源，且只属于运维配置：
    语音识别不由用户的对话模型凭据承担（BYOK 接入后这条约束必须继续成立）。
    失败原因写进 ``asr_fail_reason``，让调用方分得清「没配 key」和「转写失败」。
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.info("OPENAI_API_KEY 未设置，跳过 ASR 回退")
        return _empty_asr(asr_fail_reason=FAIL_ASR_NOT_CONFIGURED)

    audio_path = _download_audio_for_asr(url)
    if not audio_path or not os.path.exists(audio_path):
        return _empty_asr(asr_fail_reason=FAIL_ASR_FAILED)

    try:
        client = OpenAI(api_key=api_key)
        with open(audio_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
            )

        segments = []
        if hasattr(transcript, "segments") and transcript.segments:
            for seg in transcript.segments:
                segments.append({
                    "start": round(getattr(seg, "start", 0), 2),
                    "end": round(getattr(seg, "end", 0), 2),
                    "text": getattr(seg, "text", "").strip(),
                })

        full_text = getattr(transcript, "text", "") or ""
        if not full_text and segments:
            full_text = " ".join(s["text"] for s in segments)

        has = bool(full_text.strip())
        return {
            "has_subtitle": has,
            "language": "zh",
            "subtitle_type": "asr" if has else "none",
            "segments": segments if has else [],
            "full_text": full_text,
            "fail_reason": "",
            "asr_fail_reason": "" if has else FAIL_ASR_FAILED,
        }
    except Exception as e:
        logger.warning("Whisper ASR failed: %s", e)
        return _empty_asr(asr_fail_reason=FAIL_ASR_FAILED)
    finally:
        _cleanup_asr(audio_path)


def _download_audio_for_asr(url: str) -> str | None:
    """下载视频/音频文件用于 ASR，返回文件路径"""
    from downloader import VideoDownloader
    download_dir = VideoDownloader().DOWNLOAD_DIR
    os.makedirs(download_dir, exist_ok=True)

    if _is_douyin_url(url):
        from douyin import DouyinParser
        parser = DouyinParser(download_dir=download_dir)
        try:
            logger.info("Downloading Douyin video for ASR...")
            result = parser.download(url, mode="video")
            video_path = result["filepath"]
            audio_path = video_path.rsplit(".", 1)[0] + "_audio.mp3"
            _extract_audio_for_asr(video_path, audio_path)
            if os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                try:
                    os.remove(video_path)
                except OSError:
                    pass
                return audio_path
            return video_path
        except Exception as e:
            logger.warning("Douyin audio download failed: %s", e)
            return None
    else:
        try:
            logger.info("Downloading audio via yt-dlp for ASR...")
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "bestaudio/best",
                "outtmpl": os.path.join(download_dir, "%(id)s.%(ext)s"),
                "noplaylist": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                }],
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_id = info.get("id", "audio")
                for fname in os.listdir(download_dir):
                    if fname.startswith(video_id) and fname.endswith(".mp3"):
                        return os.path.join(download_dir, fname)
            return None
        except Exception as e:
            logger.warning("yt-dlp audio download failed: %s", e)
            return None


def _extract_audio_for_asr(video_path: str, output_path: str):
    """从视频文件中提取音频"""
    cmd = [_ffmpeg_path(), "-i", video_path, "-vn",
           "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
           output_path, "-y"]
    subprocess.run(cmd, capture_output=True, timeout=300)


def _cleanup_asr(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _empty_asr(language: str = "zh", asr_fail_reason: str = "") -> dict:
    return {
        "has_subtitle": False,
        "language": language,
        "subtitle_type": "none",
        "segments": [],
        "full_text": "",
        "fail_reason": "",
        "asr_fail_reason": asr_fail_reason,
    }


# ── 单次调用的分段协议 ────────────────────────────────
#: 哨兵行：它**之前**是总结正文（逐 token 下发），**之后**到流末尾是一段 JSON。
#:
#: 用哨兵而不是「等全文结束再吐」，正是为了保住打字机效果；也不能改成
#: 「让模型直接返回完整 JSON」——那样同样要等全文，而且总结就不再逐字输出了。
MINDMAP_TAGS_SENTINEL = "<<<VIDDIGEST_MINDMAP_TAGS>>>"

#: 事件类型。与 api_summarize 下发的 SSE 事件名一一对应。
EVENT_SUMMARY = "summary"
EVENT_MINDMAP = "mindmap"
EVENT_TAGS = "tags"


def parse_dual_output(chunks):
    """把一次流式响应拆成「总结正文」与「尾部 JSON」，yield ``(kind, payload)``。

    逐块喂进来：哨兵之前的块**立刻** yield 出去（打字机效果就靠这个），
    哨兵之后的部分攒起来，流结束时一次性 ``json.loads``。

    哨兵可能被切在两块之间（甚至更多块），所以每块都先按「尾部这段可能是
    哨兵的前缀」留下尾巴，剩下的才算正文——否则哨兵的一部分会被当正文吐出去。

    尾部解析失败（模型没给哨兵 / JSON 坏了）不算整次失败：总结已经拿到了，
    思维导图与标签退化成空值，交给调用方兜底。
    """
    buffer = ""
    tail: list[str] = []
    seen = False

    for chunk in chunks:
        if seen:
            tail.append(chunk)
            continue
        buffer += chunk
        at = buffer.find(MINDMAP_TAGS_SENTINEL)
        if at >= 0:
            # rstrip：哨兵独占一行，它前面那个换行属于哨兵，不属于正文
            head = buffer[:at].rstrip("\n")
            if head:
                yield EVENT_SUMMARY, head
            tail.append(buffer[at + len(MINDMAP_TAGS_SENTINEL):])
            seen = True
            buffer = ""
            continue
        keep = _hold_back_len(buffer)
        if keep < len(buffer):
            yield EVENT_SUMMARY, buffer[:len(buffer) - keep]
            buffer = buffer[len(buffer) - keep:]

    if not seen and buffer:
        # 整段流里没有哨兵：扣下的尾巴确实是正文，不能凭空吞掉
        yield EVENT_SUMMARY, buffer

    # 两条尾事件恒定发出：事件序列不该随模型是否听话而变
    mindmap, tags = _parse_tail("".join(tail))
    yield EVENT_MINDMAP, mindmap
    yield EVENT_TAGS, tags


def _hold_back_len(buffer: str) -> int:
    """buffer 末尾要扣住、暂不当正文 yield 出去的字符数。

    有两类尾巴都属于哨兵那一行：

    - **哨兵的前缀**：哨兵可能被切在两块之间（"...VIDDIGEST_MIND" +
      "MAP_TAGS>>>"），先吐出去就会把哨兵的一半混进总结。
    - **哨兵前面那个换行**：哨兵独占一行。只在哨兵到达时才 rstrip 是不够
      的——换行可能早在上一块就被当成正文吐出去了，得先扣住。
    """
    for n in range(min(len(buffer), len(MINDMAP_TAGS_SENTINEL) - 1), 0, -1):
        if MINDMAP_TAGS_SENTINEL.startswith(buffer[-n:]):
            return n
    return 1 if buffer.endswith("\n") else 0


def _parse_tail(tail: str) -> tuple[str, list]:
    """解析哨兵之后那段 JSON → ``(思维导图原文, 模型给的原始标签)``。

    这里只负责协议：把 JSON 读出来，读不出来就退回空值。
    思维导图的清洗（``clean_mindmap_markdown``）与标签的词表校验
    （``tags.validate_tags``）都在调用方做——前者是渲染要求，后者是产品规则。
    """
    # 模型偶尔会给 JSON 套上 ```json 围栏
    body = re.sub(r"^```(?:json)?\s*\n?", "", tail.strip(), flags=re.IGNORECASE)
    body = re.sub(r"\n?```\s*$", "", body).strip()
    if not body:
        logger.warning(
            "模型没有输出哨兵行 %s，思维导图与标签为空", MINDMAP_TAGS_SENTINEL
        )
        return "", []

    try:
        data = json.loads(body)
    except ValueError as e:
        logger.warning("哨兵之后的 JSON 解析失败: %s", e)
        return "", []
    if not isinstance(data, dict):
        logger.warning("哨兵之后的 JSON 不是对象（%s），思维导图与标签为空",
                       type(data).__name__)
        return "", []

    # 模型输出是不可信输入：类型不对就当没有，不让它变成下游的 TypeError
    mindmap = data.get("mindmap")
    if not isinstance(mindmap, str):
        mindmap = ""
    raw_tags = data.get("tags")
    if not isinstance(raw_tags, list):
        raw_tags = []
    return mindmap, list(raw_tags)


class VideoSummarizer:
    """
    ────────────────────────────────────────────────────────────
    LC :: AI 视频总结模块（多后端可切换）
    ────────────────────────────────────────────────────────────
    职责 : 调用 LLM API，基于字幕文本**一次**产出：
           - 结构化视频总结（流式，逐 token 推送）
           - 思维导图 Markdown（哨兵之后的 JSON，随总结同一次调用返回）
           - 标签（同一份 JSON，只能取自固定词表）
           - AI 问答（流式，基于字幕上下文）

    支持后端（按优先级读取环境变量）:
      1. 阿里云百炼 OpenAI 兼容模式（默认推荐）
           - ALIYUN_BAILIAN_API_KEY   必填
           - ALIYUN_BAILIAN_BASE_URL  可选，默认 https://dashscope.aliyuncs.com/compatible-mode/v1
           - ALIYUN_BAILIAN_MODEL     可选，默认 qwen-turbo（最便宜）
      2. DeepSeek（兼容旧配置）
           - DEEPSEEK_API_KEY         必填

    配置 : 在 backend/.env 中设置任一组即可（.env 已被 .gitignore 忽略）

    模型 : qwen-turbo / deepseek-chat 等（兼容 OpenAI ChatCompletion 接口）

    安全提醒:
      - 永远不要在代码中硬编码 API Key
      - 永远不要提交包含真实 Key 的 .env 到 Git
      - 生产环境通过系统环境变量或密钥管理服务注入
    ────────────────────────────────────────────────────────────
    """

    # 默认配置常量
    DEFAULT_BAILIAN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_BAILIAN_MODEL = "qwen-turbo"
    DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"

    def __init__(self, credential: UserCredential | None = None):
        """
        LC :: 初始化 LLM 客户端
        ──────────────────────────────
        后端选择优先级:
          1. 阿里云百炼 ALIYUN_BAILIAN_API_KEY（配置了即生效）
          2. DeepSeek DEEPSEEK_API_KEY（兜底）

        任意一组未配置都会抛出 ValueError，阻止后续调用。
        视频下载功能不受此影响。

        传入 ``credential`` 时改用**用户自带凭据**（BYOK）：只有认证头换成
        用户的，base_url 与 model 仍走平台配置。自定义 base_url 会让服务端
        向用户指定的任意地址发请求，那是 SSRF 口子，票面也没要求换服务商。
        """
        if credential is not None:
            # 平台配没配自己的 key 都不影响这条分支的取值：恒定走百炼兼容端点，
            # 运维要用别的 OpenAI 兼容服务就改 ALIYUN_BAILIAN_BASE_URL / MODEL。
            # 读平台的 key 反而会让「带没带凭据」的行为随部署配置漂移。
            self.provider = "user_credential"
            self.client = OpenAI(
                # 全仓唯一一处 reveal()。多一个调用点，就要重新解释一遍
                # 「这一行的真值会不会被谁打印」。
                api_key=credential.reveal(),
                base_url=os.getenv(
                    "ALIYUN_BAILIAN_BASE_URL",
                    self.DEFAULT_BAILIAN_BASE_URL,
                ).strip() or self.DEFAULT_BAILIAN_BASE_URL,
            )
            self.model = os.getenv(
                "ALIYUN_BAILIAN_MODEL",
                self.DEFAULT_BAILIAN_MODEL,
            ).strip() or self.DEFAULT_BAILIAN_MODEL
            return

        bailian_key = os.getenv("ALIYUN_BAILIAN_API_KEY", "").strip()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

        if bailian_key:
            # ── 阿里云百炼（OpenAI 兼容模式）──
            self.provider = "aliyun_bailian"
            self.client = OpenAI(
                api_key=bailian_key,
                base_url=os.getenv(
                    "ALIYUN_BAILIAN_BASE_URL",
                    self.DEFAULT_BAILIAN_BASE_URL,
                ).strip() or self.DEFAULT_BAILIAN_BASE_URL,
            )
            self.model = os.getenv(
                "ALIYUN_BAILIAN_MODEL",
                self.DEFAULT_BAILIAN_MODEL,
            ).strip() or self.DEFAULT_BAILIAN_MODEL
        elif deepseek_key:
            # ── DeepSeek（兼容旧配置）──
            self.provider = "deepseek"
            self.client = OpenAI(
                api_key=deepseek_key,
                base_url=self.DEFAULT_DEEPSEEK_BASE_URL,
            )
            self.model = self.DEFAULT_DEEPSEEK_MODEL
        else:
            # ── 两个都没配置 ──
            raise ValueError(
                "未检测到 AI 服务 API Key，请配置以下任一组环境变量：\n"
                "  ├─ 阿里云百炼（推荐，最便宜）:\n"
                "  │   ALIYUN_BAILIAN_API_KEY=sk-xxx\n"
                "  │   ALIYUN_BAILIAN_MODEL=qwen-turbo   # 可选，默认 qwen-turbo\n"
                "  └─ DeepSeek（兼容）:\n"
                "      DEEPSEEK_API_KEY=sk-xxx\n"
                "  获取地址: https://bailian.console.aliyun.com/  或  https://platform.deepseek.com/api_keys"
            )

    # ── 总结 + 思维导图 + 标签（一次调用）────────────────

    def summarize_full_stream(self, subtitle_text: str, language: str = "zh"):
        """一次模型调用产出三件事，yield ``(kind, payload)`` 事件。

        事件顺序：``("summary", token)`` 若干（逐 token，对应打字机效果），
        然后 ``("mindmap", markdown)`` 与 ``("tags", 模型给的原始标签)``。

        思维导图在这里就洗过一遍（markmap 渲染要求唯一一级根标题）。
        标签**不校验**：词表是产品规则，由 api_summarize 在下发前用
        ``tags.validate_tags`` 兜住——模型自创的词到不了线上。
        """
        chunks = self._stream_chunks(self._build_full_prompt(subtitle_text, language))
        for kind, payload in parse_dual_output(chunks):
            if kind == EVENT_MINDMAP:
                payload = self.clean_mindmap_markdown(payload)
            yield kind, payload

    def _stream_chunks(self, prompt: str):
        """把一次流式响应摊平成纯文本块。"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个专业的视频内容分析助手。你在一次回复里同时给出"
                        "结构化总结、思维导图和标签，并严格遵守输出格式。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            stream=True,
            temperature=0.7,
            max_tokens=4096,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    @staticmethod
    def clean_mindmap_markdown(md: str) -> str:
        """清洗 LLM 输出的思维导图 Markdown：
        1. 去除代码块围栏；2. 截掉第一个标题前的说明文字；
        3. 确保存在唯一的 `# ` 一级根标题（markmap 渲染必需）。
        """
        if not md or not md.strip():
            return md or ""
        text = md.strip()
        # 1. 去除代码块围栏
        text = re.sub(r"^```(?:markdown|md)?\s*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```\s*$", "", text)
        # 2. 截掉第一个标题之前的前导说明文字
        m = re.search(r"^#{1,6}\s+\S", text, flags=re.MULTILINE)
        if m:
            text = text[m.start():]
            # 3. 确保存在 `# ` 一级根标题
            if not re.search(r"^#\s+\S", text, flags=re.MULTILINE):
                first_h2 = re.search(r"^##\s+(.+)$", text, flags=re.MULTILINE)
                if first_h2:
                    # 将第一个二级标题提升为一级根标题
                    text = f"# {first_h2.group(1).strip()}\n" + text[first_h2.end():]
                else:
                    text = "# 视频思维导图\n" + text
            return text.strip()
        # 4. 兜底：没有任何 Markdown 标题时，把非空文本行转为根节点下的列表
        items = []
        for line in text.splitlines():
            clean = re.sub(r"^[-*•#\d.、\s]+", "", line.strip()).strip()
            if clean:
                items.append(f"- {clean}")
        if items:
            return "# 视频思维导图\n" + "\n".join(items[:50])
        return "# 视频思维导图\n- 暂无内容"

    # ── AI 问答 ────────────────────────────────────────

    def chat_stream(self, subtitle_text: str, question: str, history=()):
        """基于视频内容的 AI 问答，流式返回。

        ``history`` 是**更早**几轮的 ``(role, content)`` 序列，让模型接得住
        「刚才那个」这类指代。带默认值是必须的：解析路径之外没有别的地方
        调它，不给默认值等于逼所有调用方都关心上下文。
        """
        messages = self._build_chat_prompt(subtitle_text, question, history)
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=0.7,
            max_tokens=2048,
        )
        for chunk in response:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    # ── Prompt 设计 ────────────────────────────────────

    @staticmethod
    def _build_full_prompt(subtitle_text: str, language: str) -> str:
        """一次调用要涵盖三件事的格式要求，外加分段协议的硬规定。

        词表原样拼进 prompt：模型只能从里面选。prompt 只是「告知」，
        真正的拦截在 tags.validate_tags（服务端），两层缺一不可。
        """
        truncated = subtitle_text[:15000]
        lang_hint = "中文" if language.startswith("zh") else "与原文相同的语言"
        return f"""请对以下视频字幕内容做一次分析，一次给出三样东西：总结、思维导图、标签。使用{lang_hint}输出。

【第一部分：总结正文】
1. 必须输出标准 Markdown，并使用多级标题构建清晰的层级结构：
   - `# 视频主题`：一级标题，全文有且仅有一个；
   - `## 章节`：二级标题作为主要章节（如 视频概述 / 内容大纲 / 核心知识要点 / 总结）；
   - `### 小节`：三级标题用于章节内部的细分要点。
2. 严禁用 `**加粗**` 代替标题，所有章节标题都必须以 # 号开头独占一行。
3. 章节内部的要点使用无序列表（`- `）呈现，子要点通过缩进（`  - `）形成层级，禁止整段堆砌文字。
4. 只输出 Markdown 正文，不要输出代码块围栏，也不要任何解释性文字。

【第二部分：思维导图】
只输出 Markdown 标题层级结构，禁止代码块围栏、禁止任何说明、问候或总结性文字：
- `# 视频主题`：一级标题，有且仅有一个，作为导图中心主题；
- `## 主要章节`：二级标题，作为一级分支（一般 3-6 个）；
- `### 次级要点`：三级标题，作为二级分支；
- `- 细节要点`：无序列表，作为叶子节点（可选）。
每个节点文字尽量不超过 15 个字，全图节点总数控制在 30 个以内。

【第三部分：标签】
从下列固定词表中选出最多 3 个最贴切的标签，必须用词表里的中文原词，
不要自创近义词（例如不要写「AI 编程」「编程教学」）：
{vocabulary_prompt_text()}
确实没有合适的就只选「其他」。

【输出格式 —— 必须严格遵守】
第一部分正文之后，另起一行输出哨兵行（一个字都不能改，也不要加代码块围栏）：
{MINDMAP_TAGS_SENTINEL}
紧接着输出一行 JSON，形如：
{{"mindmap": "# 视频思维导图\\n## 章节\\n### 要点", "tags": ["编程", "人工智能"]}}

完整示例：
# 视频主题
## 视频概述
（总结正文……）
{MINDMAP_TAGS_SENTINEL}
{{"mindmap": "# 视频主题\\n## 章节一\\n### 要点一", "tags": ["编程"]}}

---
视频字幕内容：
{truncated}"""

    @staticmethod
    def _build_chat_prompt(subtitle_text: str, question: str, history=()) -> list:
        """构造追问的 messages 数组。

        结构：system → 字幕块（首条 user，沿用改动前就有的做法）→ 历史
        user/assistant 交替 → 本轮 question 独占最后一条。

        本轮问题放在最后而不是像改动前那样拼进字幕块，是因为历史一旦接进来，
        混在中间的「用户问题：…」会让模型分不清哪句是这次问的、哪句是上轮问的。
        字幕块那侧的「请基于视频内容给出准确、详细的回答」留在原处不动。
        """
        truncated = subtitle_text[:12000]
        messages = [{
            "role": "system",
            "content": "你是一个视频内容问答助手。根据提供的字幕内容回答用户的问题。",
        }, {
            "role": "user",
            "content": f"""以下是一个视频的字幕内容，请根据这些内容回答用户的问题。

视频字幕内容：
{truncated}

---
请基于视频内容给出准确、详细的回答。如果视频内容中没有相关信息，请诚实说明。""",
        }]
        for role, content in history:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        return messages
