# VidDigest 后端镜像（工单 #63）
#
# 三条硬约束写在这里，是为了下次改镜像时不被无声破坏：
#   1. 非 root 运行——容器里的进程不该是 root。
#   2. 自带 ffmpeg——yt-dlp 合并分离的音视频流要靠它，缺了只能退回单流下载。
#   3. 带 healthcheck——compose 的 depends_on 与巡检脚本都靠它判活。
#
# 构建上下文是 ./backend（见 docker-compose.yml），所以这里不需要写路径前缀。
FROM python:3.11-slim

# ffmpeg：合并 DASH 源分离出来的视频流与音频流
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖先装：只有 requirements.txt 变化时，这一层才需要重建
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 非 root：uid 固定 10001，容器内外都能对上同一个用户
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIDDIGEST_LOG_LEVEL=INFO \
    VIDDIGEST_LOG_DIR=/app/logs

EXPOSE 8000

# 健康检查用标准库 urllib，不额外装东西
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
