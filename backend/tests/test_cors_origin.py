"""跨域白名单的守卫。

**为什么值得有守卫**：CORS 是本仓库唯一一处「配置写错就等于对外开门」的地方，
而它曾经就是 ``allow_origins=["*"]`` + ``allow_credentials=True``——
starlette 在这个组合下会把请求的 ``Origin`` **原样回显**进
``Access-Control-Allow-Origin``，等于对任意来源开放带凭据的跨域读取。
本仓库的鉴权走 Cookie 里的 JWT。

判据落在**浏览器真正看的那几个响应头**上，不测私有函数：
``parse_allowed_origins`` 被改名或换写法都不该让这些断言变红；反过来，
把白名单改回通配符、或让预检放行，都**必须**转红。
"""
import ast
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import cors_config
from seams import make_client

ALLOWED = "http://allowed.example"
OTHER = "http://other.example"


def _app(raw):
    """按给定的白名单串造一个最小 app。

    刻意不 import 真正的 ``main``：那里的中间件在 import 时就固化了当时的环境变量，
    测试就没法控制「白名单为空」这一状态。
    """
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **cors_config.cors_kwargs(raw))

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_empty_allowlist_emits_no_cors_headers():
    """默认（空白名单）：请求带 Origin 也不回显任何跨域头，预检被拒。"""
    client = make_client(_app(""))
    resp = client.get("/ping", headers={"Origin": ALLOWED})
    assert "access-control-allow-origin" not in resp.headers
    pre = client.options(
        "/ping",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )
    assert pre.status_code == 400


def test_allowlisted_origin_is_echoed_with_credentials():
    """白名单内的来源：回显该来源并带上凭据许可头。"""
    client = make_client(_app(ALLOWED))
    resp = client.get("/ping", headers={"Origin": ALLOWED})
    assert resp.headers.get("access-control-allow-origin") == ALLOWED
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_origin_outside_allowlist_is_not_echoed():
    """白名单外的来源：不回显、预检被拒。"""
    client = make_client(_app(ALLOWED))
    resp = client.get("/ping", headers={"Origin": OTHER})
    assert "access-control-allow-origin" not in resp.headers
    pre = client.options(
        "/ping",
        headers={"Origin": OTHER, "Access-Control-Request-Method": "GET"},
    )
    assert pre.status_code == 400


def test_parser_drops_blanks_and_duplicates():
    """解析器：空串 → 空列表；空白与空项丢弃；重复项只留第一次。"""
    assert cors_config.parse_allowed_origins(None) == []
    assert cors_config.parse_allowed_origins("") == []
    assert cors_config.parse_allowed_origins(" http://a , ,http://b ,http://a ") == [
        "http://a",
        "http://b",
    ]


def test_main_does_not_register_wildcard_origins():
    """结构守卫：``main.py`` 里不许再出现字面量通配符的 allow_origins。

    为什么要这条：前面几条断言只钉住本模块的行为，而真正上中间件的是 ``main.py``。
    有人把那里的 ``allow_origins`` 改回 ``["*"]``，白名单模块照样绿——那时候
    绿的就不是被测物了。**守卫必须落在东西会被改坏的那一处。**
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "main.py")
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_middleware"):
            continue
        for kw in node.keywords:
            if kw.arg != "allow_origins":
                continue
            value = kw.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                items = [e.value for e in value.elts if isinstance(e, ast.Constant)]
                if "*" in items:
                    offenders.append("allow_origins 传了通配符列表")
            elif isinstance(value, ast.Constant) and value.value == "*":
                offenders.append("allow_origins 传了 '*'")
    assert not offenders, "main.py 里出现了通配符跨域：" + "；".join(offenders)
