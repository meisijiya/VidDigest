"""运行期巡检：健康端点、数据库、数据目录占用、日志新鲜度（工单 #63）。

**这个脚本替代什么**：一张贴在墙上的检查清单。清单靠人记得做，
巡检脚本可以定时跑、可以在 CI 里跑、可以在「服务刚重启」的时候跑。

**判据是硬的**：任何一项不达标就退出码非零。巡检报告如果永远显示
「一切正常」，那它就只是一段输出文字，不是判据。

检查项：
    1. 健康端点可访问且返回 200（默认 http://127.0.0.1:8000/api/health）
    2. 数据库文件存在，且 PRAGMA integrity_check 通过
    3. 数据目录占用不超过阈值（默认 1024 MB）
    4. 日志文件存在且新鲜（默认 24 小时内有写入）
    5. 前端入口页不许缓存（默认 http://127.0.0.1:8080/）

用法：
    python scripts/ops/inspect_runtime.py
    python scripts/ops/inspect_runtime.py --health-url http://localhost:8080/api/health
    python scripts/ops/inspect_runtime.py --frontend-url http://localhost:8080/

退出码：0 = 全部达标；1 = 有项目不达标。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(REPO, "backend", "data", "app.db")
DEFAULT_DATA_DIR = os.path.join(REPO, "backend", "data")
DEFAULT_LOG = os.path.join(REPO, "backend", "logs", "app.log")
DEFAULT_HEALTH_URL = "http://127.0.0.1:8000/api/health"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:8080/"
DEFAULT_MAX_DATA_MB = 1024
DEFAULT_MAX_LOG_AGE_HOURS = 24

# Windows 控制台默认不是 UTF-8，中文路径与提示会变成乱码——输出要给人看
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def dir_size_mb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total / (1024 * 1024)


def check_health(url, timeout=5):
    """健康端点：状态码必须是 200。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            if resp.status != 200:
                return False, "HTTP %s" % resp.status
            try:
                payload = json.loads(body)
            except ValueError:
                return False, "响应不是 JSON：%s" % body[:120]
            return True, "200 %s" % payload
    except urllib.error.URLError as exc:
        return False, "连不上：%s" % exc
    except Exception as exc:  # noqa: BLE001 — 巡检要的是「任何异常都算不达标」
        return False, "异常：%s" % exc


def check_db(path):
    if not os.path.isfile(path):
        return False, "文件不存在：%s" % path
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return False, "打不开：%s" % exc
    if not row or str(row[0]).strip().lower() != "ok":
        return False, "integrity_check：%s" % (row[0] if row else "无结果")
    size_mb = os.path.getsize(path) / (1024 * 1024)
    return True, "integrity_check ok（%.1f MB）" % size_mb


def check_data_dir(path, max_mb):
    if not os.path.isdir(path):
        return False, "目录不存在：%s" % path
    used = dir_size_mb(path)
    if used > max_mb:
        return False, "占用 %.1f MB，超过阈值 %d MB" % (used, max_mb)
    return True, "占用 %.1f MB（阈值 %d MB）" % (used, max_mb)


def check_log_freshness(path, max_age_hours):
    if not os.path.isfile(path):
        return False, "日志文件不存在：%s" % path
    age_hours = (time.time() - os.path.getmtime(path)) / 3600.0
    if age_hours > max_age_hours:
        return False, "最后写入在 %.1f 小时前，超过阈值 %d 小时" % (age_hours, max_age_hours)
    return True, "最后写入在 %.1f 小时前（阈值 %d 小时）" % (age_hours, max_age_hours)


def check_frontend_entry_not_cached(url, timeout=5):
    """前端入口页必须回源。

    发一版新前端，浏览器手里还捏着旧的 index.html，于是去加载旧 bundle——
    页面「改了没生效」，排查半天才发现是缓存，不是代码。静态资源带内容哈希，
    可以长期缓存；入口页不行。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            cache_control = resp.headers.get("Cache-Control", "") or ""
    except Exception as exc:  # 连不上、超时、非 200 都算不达标
        return False, "取不到前端入口：%s" % exc
    if "no-store" not in cache_control.lower():
        return False, "前端入口没有 no-store（Cache-Control=%r）" % cache_control
    return True, "前端入口标了 no-store（Cache-Control=%r）" % cache_control


def main(argv=None):
    parser = argparse.ArgumentParser(description="VidDigest 运行期巡检")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    parser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    parser.add_argument("--log", default=DEFAULT_LOG)
    parser.add_argument("--max-data-mb", type=int, default=DEFAULT_MAX_DATA_MB)
    parser.add_argument("--max-log-age-hours", type=int, default=DEFAULT_MAX_LOG_AGE_HOURS)
    args = parser.parse_args(argv)

    checks = [
        ("健康端点", check_health(args.health_url)),
        ("数据库", check_db(args.db)),
        ("数据目录", check_data_dir(args.data_dir, args.max_data_mb)),
        ("日志新鲜度", check_log_freshness(args.log, args.max_log_age_hours)),
        ("前端入口缓存", check_frontend_entry_not_cached(args.frontend_url)),
    ]

    failed = 0
    print("VidDigest 运行期巡检")
    print("-" * 60)
    for name, (ok, detail) in checks:
        print("[%s] %-12s %s" % ("OK  " if ok else "FAIL", name, detail))
        if not ok:
            failed += 1
    print("-" * 60)
    if failed:
        print("巡检不通过：%d 项不达标" % failed)
        return 1
    print("巡检通过：全部达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
