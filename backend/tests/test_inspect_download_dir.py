"""巡检脚本的第 6 项判据：下载目录（工单 #90）。

## 为什么这个脚本此前没有用例

`init.sh` 的 pytest 只收集 `backend/tests/`，而运维脚本一直靠**人肉跑**
验证：跑一次、看一眼输出、相信它。

## 判据读的是外部可观察的东西

- 巡检的退出码（0 = 全部达标，1 = 有项目不达标，2 = 参数写错）
- 巡检打印出来的字（实际占用、清理办法、清掉几个）
- 磁盘上还有没有那个超龄文件

## 其余五项为什么也在这里跑一遍

巡检多了一项，不等于可以顺手改掉别的项的行为。这里把五项原判据
在同一个进程里跑一遍：它们的名字、顺序、结论都得和从前一样。
"""
import importlib.util
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "ops" / "inspect_runtime.py"


def _load_inspect_module():
    """按路径加载脚本（它不在包里，也不是可导入的模块）。"""
    spec = importlib.util.spec_from_file_location("ops_inspect_runtime", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inspect_runtime = _load_inspect_module()


class _Handler(BaseHTTPRequestHandler):
    """一个只够巡检用的本地服务：健康端点 + 前端入口页。"""

    def do_GET(self):
        if self.path.startswith("/api/health"):
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.startswith("/cached"):
            body = b"<html></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "max-age=3600")
        else:
            body = b"<html></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 别把访问日志糊满 pytest 输出
        pass


@pytest.fixture()
def service():
    """起一个真的本地 HTTP 服务——巡检要打的是真的 HTTP 响应。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def world(tmp_path, service):
    """一套「五项原判据都达标」的环境，外加下载目录的落点。"""
    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.commit()
    finally:
        conn.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log_path = tmp_path / "app.log"
    log_path.write_text("boot\n", encoding="utf-8")
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    def argv(*extra):
        return [
            "--health-url", service + "/api/health",
            "--frontend-url", service + "/",
            "--db", str(db_path),
            "--data-dir", str(data_dir),
            "--log", str(log_path),
            "--download-dir", str(downloads),
        ] + list(extra)

    return {"argv": argv, "downloads": downloads, "db": db_path, "service": service}


def _line_of(output, name):
    """取出某一项判据的打印行。"""
    for line in output.splitlines():
        if name in line:
            return line
    raise AssertionError("巡检输出里没有 %s 这一项：\n%s" % (name, output))


def _write_mb(directory, name, size_mb):
    path = directory / name
    path.write_bytes(b"0" * int(size_mb * 1024 * 1024))
    return path


# ── 1. 占用看得见 ────────────────────────────────────────────


def test_the_download_dir_usage_is_reported(world, capsys):
    """下载目录占了多少，巡检要说得出来。"""
    _write_mb(world["downloads"], "big.mp4", 2)

    rc = inspect_runtime.main(world["argv"]())
    out = capsys.readouterr().out

    assert rc == 0, out
    line = _line_of(out, "下载目录")
    assert "占用" in line, line
    assert "2.0 MB" in line, line


# ── 2. 超阈不通过 ────────────────────────────────────────────


def test_an_over_threshold_download_dir_fails_the_inspection(world, capsys):
    """占用超阈就是不达标，退出码不能还是 0。"""
    _write_mb(world["downloads"], "big.mp4", 2)

    rc = inspect_runtime.main(world["argv"]("--max-download-mb", "1"))
    out = capsys.readouterr().out

    assert rc == 1, out
    assert "FAIL" in _line_of(out, "下载目录")


def test_the_failure_prints_the_usage_and_how_to_clean_it(world, capsys):
    """不达标时要能照着做：实际占多少、怎么清。"""
    _write_mb(world["downloads"], "big.mp4", 2)

    rc = inspect_runtime.main(world["argv"]("--max-download-mb", "1"))
    out = capsys.readouterr().out

    assert rc == 1, out
    line = _line_of(out, "下载目录")
    assert "2.0 MB" in line, line
    assert "清理办法" in line, line


# ── 3. 顺手清超龄残留 ────────────────────────────────────────


def test_stale_residue_is_removed_and_counted(world, capsys):
    """超龄残留会被收走，收了几个要报出来。"""
    stale = _write_mb(world["downloads"], "old.mp4", 1)
    old = time.time() - 7200
    os.utime(stale, (old, old))

    rc = inspect_runtime.main(world["argv"]("--max-download-age-hours", "1"))
    out = capsys.readouterr().out

    assert rc == 0, out
    assert not stale.exists(), "超龄残留没有被收走"
    assert "清掉超龄残留 1 个" in _line_of(out, "下载目录")


def test_fresh_residue_is_kept(world, capsys):
    """反向对照：没超龄的文件不许被收走。"""
    fresh = _write_mb(world["downloads"], "new.mp4", 1)

    rc = inspect_runtime.main(world["argv"]("--max-download-age-hours", "1"))
    out = capsys.readouterr().out

    assert rc == 0, out
    assert fresh.exists(), "还没超龄的文件被收走了"
    assert "清掉超龄残留" not in _line_of(out, "下载目录")


# ── 4. 阈值可调 ──────────────────────────────────────────────


def test_the_usage_threshold_is_configurable(world, capsys):
    """同一个目录，占用阈值给宽就给过，给紧就不过。"""
    _write_mb(world["downloads"], "big.mp4", 2)

    assert inspect_runtime.main(world["argv"]("--max-download-mb", "4096")) == 0
    assert inspect_runtime.main(world["argv"]("--max-download-mb", "1")) == 1


def test_the_age_threshold_is_configurable(world):
    """超龄时长可调：给宽了旧文件留着，给紧了才收。"""
    stale = _write_mb(world["downloads"], "old.mp4", 1)
    old = time.time() - 7200
    os.utime(stale, (old, old))

    assert inspect_runtime.main(world["argv"]("--max-download-age-hours", "48")) == 0
    assert stale.exists()

    assert inspect_runtime.main(world["argv"]("--max-download-age-hours", "1")) == 0
    assert not stale.exists()


def test_the_age_threshold_may_not_go_below_one_hour(world):
    """超龄阈值的下限是 1 小时：ASR 通道的 ffmpeg 超时是 300 秒。

    阈值比转码还短，巡检会在转码还没结束时把文件删掉。所以这个下限
    是硬伤，不是口味问题——参数写错要当场报错，不能默默照用。
    """
    with pytest.raises(SystemExit) as excinfo:
        inspect_runtime.main(world["argv"]("--max-download-age-hours", "0"))

    assert excinfo.value.code == 2


# ── 5. 原五项判据不变 ────────────────────────────────────────


def test_the_five_original_checks_keep_their_names_and_order(world, capsys):
    """新判据是**追加**的，不是把原来的挤掉一位。"""
    rc = inspect_runtime.main(world["argv"]())
    out = capsys.readouterr().out

    assert rc == 0, out
    order = [
        "健康端点", "数据库", "数据目录", "日志新鲜度", "前端入口缓存", "下载目录",
    ]
    positions = [out.index(name) for name in order]
    assert positions == sorted(positions), out


def test_a_broken_database_still_fails_the_inspection(world, capsys):
    """数据库坏了还是判不通过——原判据的行为没被顺手改掉。"""
    world["db"].write_bytes(b"not a sqlite file at all")

    rc = inspect_runtime.main(world["argv"]())
    out = capsys.readouterr().out

    assert rc == 1, out
    assert "FAIL" in _line_of(out, "数据库")


def test_a_cached_frontend_entry_still_fails_the_inspection(world, capsys):
    """前端入口页带缓存还是判不通过。"""
    argv = world["argv"]()
    argv[argv.index("--frontend-url") + 1] = world["service"] + "/cached"

    rc = inspect_runtime.main(argv)
    out = capsys.readouterr().out

    assert rc == 1, out
    assert "FAIL" in _line_of(out, "前端入口缓存")
