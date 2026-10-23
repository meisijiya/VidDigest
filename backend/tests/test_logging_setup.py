"""日志配置的外部可观察行为（工单 #63）。

断言的是「日志落在哪、内容长什么样」，不是「调用了哪个函数」——
实现换了，只要外部行为不变，这几条就该保持绿。

三件事各有一组形状互补的用例：
  1. 文件：什么级别都进，落盘是一行一条 JSON
  2. 控制台：只出警告及以上（stdout/stderr 是协议面，正常请求期间必须干净）
  3. 幂等与流：装两次不重复写；写的是**当前**的 stderr，不是构造那一刻的
"""
import io
import json
import logging

import pytest

from logging_setup import setup_logging


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """把日志文件指到临时目录，并在结束后还原根 logger 的状态。"""
    target = tmp_path / "app.log"
    monkeypatch.setenv("VIDDIGEST_LOG_FILE", str(target))
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield target
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "_viddigest", False):
                root.removeHandler(handler)
                handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def read_lines(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_file_receives_every_level_as_one_json_line_per_record(log_file, capfd):
    setup_logging()

    logging.getLogger("httpx2").info("第三方噪音")
    logging.getLogger("api_summarize").warning("业务告警")

    capfd.readouterr()
    records = read_lines(log_file)
    by_msg = {r["msg"]: r for r in records}
    assert "第三方噪音" in by_msg, f"文件里没有 INFO 记录：{records}"
    assert "业务告警" in by_msg, f"文件里没有 WARNING 记录：{records}"
    assert by_msg["业务告警"]["level"] == "WARNING"
    assert by_msg["第三方噪音"]["logger"] == "httpx2"
    assert set(by_msg["业务告警"]) >= {"ts", "level", "logger", "msg"}


def test_console_carries_warnings_but_not_info_noise(log_file, capfd):
    """控制台只出警告及以上。

    这条不是审美偏好：stdout/stderr 是应用与宿主的协议面，
    正常请求期间往那里写东西，会打中「凭据与异常文本不得进入标准输出/错误」
    那条断言（见 backend/tests/test_byok_chat.py 的三件套）。
    """
    setup_logging()

    logging.getLogger("httpx2").info("第三方噪音")
    logging.getLogger("api_summarize").warning("业务告警")

    captured = capfd.readouterr()
    assert "第三方噪音" not in captured.err, f"INFO 噪音进了标准错误：{captured.err!r}"
    assert "业务告警" in captured.err, f"警告没进标准错误：{captured.err!r}"


def test_console_writes_to_the_current_stderr(log_file, monkeypatch):
    """写的是**当前**的 stderr，不是装 handler 那一刻的。

    宿主（测试框架、容器编排）会换掉甚至关掉 stderr；
    绑死在旧对象上的 handler 会在那之后报
    「ValueError: I/O operation on closed file」，而这条报错本身
    又会污染「日志里没有异常」这类断言。
    """
    setup_logging()
    buffer = io.StringIO()
    monkeypatch.setattr("sys.stderr", buffer)

    logging.getLogger("api_summarize").warning("换流之后也要写得出去")

    assert "换流之后也要写得出去" in buffer.getvalue()


def test_setup_logging_twice_does_not_duplicate_records(log_file, capfd):
    """装两次不重复写。

    应用可能在同一个进程里起停多次（lifespan 重跑、测试里反复建 app）；
    每次都往根 logger 上叠一份 handler 的话，同一条记录会在文件里出现多行——
    重复行比没有行更难查。
    """
    setup_logging()
    setup_logging()

    logging.getLogger("api_summarize").warning("只该出现一次")

    capfd.readouterr()
    records = read_lines(log_file)
    hits = [r for r in records if r["msg"] == "只该出现一次"]
    assert len(hits) == 1, f"同一条记录写了 {len(hits)} 遍：{records}"
