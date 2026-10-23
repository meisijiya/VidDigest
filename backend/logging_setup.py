"""日志配置：JSON 行、分级、轮转（工单 #63）。

**为什么要有这个模块**：上一轮盘点里，「运维证据链」这一栏是 0——
没有结构化日志，出了问题只能看终端回滚。没有日志的部署等于
「部署过了」，不是「可运维」。

**为什么是 JSON 行**：这份日志的读者主要是机器——巡检脚本要按时间
字段判新鲜度，出事后要按级别筛。人读的格式（「2026-10-23 15:00:00
INFO ...」）在多行异常栈上会断行，一行一条 JSON 就不存在边界猜测。

**为什么轮转**：不轮转的日志会把磁盘写满，而写满的磁盘会让
数据库写失败——故障会从「日志」蔓延到「业务」。默认单文件 10 MB、
保留 5 份。

**关于凭据**：这个模块只负责「记什么、记到哪」，不做脱敏。脱敏发生在
两个更靠近数据的地方：请求体里的 user_api_key 由 main.py 的
RequestValidationError 处理器遮蔽（见 backend/main.py），落库的 BYOK
密钥不落盘（ADR 0004）。在这里做一层正则脱敏只会给出「看起来安全」
的假象——真要防住，日志里就不该出现请求体。
"""
import json
import logging
import logging.handlers
import os
import time

LOG_DIR_ENV = "VIDDIGEST_LOG_DIR"
LOG_FILE_ENV = "VIDDIGEST_LOG_FILE"
LOG_LEVEL_ENV = "VIDDIGEST_LOG_LEVEL"

DEFAULT_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
DEFAULT_LOG_FILE = "app.log"
DEFAULT_LEVEL = "INFO"

# 单个日志文件上限与保留份数：10 MB × 5 份 = 最多 50 MB
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

# 时间用 UTC：容器、CI、开发者本机的时区各不相同，
# 只有统一成 UTC，日志顺序才和真实顺序一致。
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"

# 这些 logger 归 uvicorn 所有。uvicorn 启动时会用自己的一套配置覆盖它们，
# 所以本模块在应用启动之后（lifespan startup）再接管，把 handler 换成同一套。
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonLineFormatter(logging.Formatter):
    """把一条日志渲染成一行 JSON。"""

    def format(self, record):
        payload = {
            "ts": time.strftime(TS_FORMAT, time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def resolve_level(level=None):
    """把级别名解析成 logging 的数值；认不出来就退回 INFO。"""
    raw = level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LEVEL
    value = logging.getLevelName(str(raw).strip().upper())
    if not isinstance(value, int):
        return logging.INFO
    return value


def resolve_log_file():
    """日志文件位置：环境变量优先，其次 backend/logs/app.log。"""
    explicit = os.environ.get(LOG_FILE_ENV)
    if explicit:
        return os.path.abspath(explicit)
    log_dir = os.environ.get(LOG_DIR_ENV) or DEFAULT_LOG_DIR
    return os.path.join(os.path.abspath(log_dir), DEFAULT_LOG_FILE)


def setup_logging(level=None):
    """接管根 logger 与 uvicorn 的三个 logger，返回日志文件路径。

    调用时机：应用启动之后（lifespan startup）。uvicorn 在更早的时候
    用自己的配置装了一遍 handler，不覆盖的话，同一条记录会按两种
    格式各写一遍——日志文件里出现重复行，比没有日志更难查。
    """
    log_file = resolve_log_file()
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    formatter = JsonLineFormatter()
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(resolve_level(level))
    # 只摘掉本模块装过的 handler，别把宿主（pytest、uvicorn）装的删了
    for handler in list(root.handlers):
        if getattr(handler, "_viddigest", False):
            root.removeHandler(handler)
    for handler in (file_handler, stream_handler):
        handler._viddigest = True
        root.addHandler(handler)

    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(root.level)
        # propagate=False：否则同一条记录会被 logger 自己的 handler 写一遍，
        # 再经 root 转发写第二遍——日志文件里出现重复行，比没有日志更难查。
        # uvicorn 自己的默认配置也是 propagate=False。
        logger.propagate = False
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)

    return log_file
