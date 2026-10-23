"""跨域白名单：默认不开，按环境变量显式开口。

**为什么不再是 `allow_origins=["*"]`**：`*` 与 `allow_credentials=True` 同时存在时，
starlette 的 ``CORSMiddleware`` 会把请求里的 ``Origin`` **原样回显**进
``Access-Control-Allow-Origin``，等于对任意来源开放带凭据的跨域读取。
本仓库的鉴权走 Cookie 里的 JWT（见 ``backend/auth.py``），
凭据跨域被回显即可被任意站点读走。

**为什么默认是空而不是默认 localhost**：默认空 = 默认不跨域；
确实要跨域部署时用 ``VIDDIGEST_ALLOWED_ORIGINS`` 显式开口（逗号分隔）。
开发期与生产部署都是同源——前端走 Vite 代理（``frontend/vite.config.js``
把 ``/api`` 代理到 ``http://localhost:8000``），生产走 Nginx 同域托管 + API 反代
（``docs/OPERATIONS.md``）——因此默认空不影响现有流程。

**这个模块为什么单独存在**：白名单的解析与「配置怎么落到中间件上」是两件事，
分开之后断言可以落在「浏览器真正看的那几个响应头」上，不必 import 整个 app。
"""
import os

#: 环境变量名。多个来源用逗号分隔。
ENV_KEY = "VIDDIGEST_ALLOWED_ORIGINS"


def parse_allowed_origins(raw):
    """把逗号分隔的来源串解析成去重、去空白的列表。

    - ``None`` / 空串 → 空列表（不开跨域）
    - 每一项去掉首尾空白；空项丢弃（``a,,b`` 不产生空串来源）
    - 重复项只留第一次出现的
    """
    if not raw:
        return []
    origins = []
    for item in raw.split(","):
        item = item.strip()
        if item and item not in origins:
            origins.append(item)
    return origins


def cors_kwargs(raw=None):
    """给 ``app.add_middleware(CORSMiddleware, **cors_kwargs())`` 用的关键字参数。

    ``raw`` 省略时从环境变量 ``VIDDIGEST_ALLOWED_ORIGINS`` 读；显式传字符串则用它。
    """
    if raw is None:
        raw = os.environ.get(ENV_KEY, "")
    return {
        "allow_origins": parse_allowed_origins(raw),
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
