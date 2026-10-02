"""测试接缝：桩、线程连接清理、真实 HTTP 客户端。

社区功能的测试靠这三条接缝守住行为。这里集中定义，是为了让「守不住」这件事
在编写测试时就能被发现，而不是让假通过的断言扩散到后面八张工单。

三条接缝各自回答一个问题：

1. ``StubSummarizer`` —— 模型到底被调了哪个方法、调了几次。
   早期版本只有一个总计数，且思维导图方法不计数，于是「重复解析时未调用模型」
   这类断言会假通过：实现跳过了总结却仍调用思维导图，总计数依然是 0。
2. ``close_all_thread_connections()`` —— 工作线程的库连接必须清干净。
   路由层用 ``run_in_executor`` 把工作派发到线程池，连接按线程缓存，
   只清主测试线程的话，下一个测试会连到上一个测试的临时库。
3. ``make_client()`` —— 鉴权依赖必须真的被解析。
   直接调用路由函数时，``user`` 参数拿到的是依赖对象本身而非用户或空值，
   「传空用户测 401」只是手写了依赖的返回值，没有测到鉴权行为。
"""
import sqlite3

from fastapi.testclient import TestClient

import database


# ── 接缝一：按方法计数的桩 ─────────────────────────────────

#: 会被计数的模型方法。逐方法断言必须能区分它们，否则「未调用模型」不可证。
MODEL_METHODS = ("summarize_stream", "generate_mindmap", "chat_stream")


class StubSummarizer:
    """替换 VideoSummarizer —— 不联网、不花钱、可重复。

    ``calls_of(method)`` 给出单个方法的调用次数，断言时逐方法检查。
    ``calls`` 保留为所有方法之和，方便一眼看出「总共调过没有」，
    但它**不能**单独用来断言「某个方法没被调用」。
    """

    def __init__(self, summary_tokens=("tok-a", "tok-b"), mindmap="# mindmap",
                 answer_tokens=("answer-1",)):
        self._summary_tokens = tuple(summary_tokens)
        self._mindmap = mindmap
        self._answer_tokens = tuple(answer_tokens)
        self._calls = {m: 0 for m in MODEL_METHODS}

    def calls_of(self, method: str) -> int:
        """单个模型方法的调用次数。未知方法名直接报错，避免拼错后静默返回 0。"""
        if method not in self._calls:
            raise KeyError(
                f"未知的模型方法 {method!r}；可计数的：{sorted(self._calls)}"
            )
        return self._calls[method]

    @property
    def calls(self) -> int:
        """所有模型方法调用次数之和。用于粗筛，不用于逐方法断言。"""
        return sum(self._calls.values())

    def called_methods(self) -> set:
        """被调用过的方法名集合。"""
        return {m for m, n in self._calls.items() if n > 0}

    def summarize_stream(self, text, language):
        self._calls["summarize_stream"] += 1
        yield from self._summary_tokens

    def generate_mindmap(self, text, language):
        self._calls["generate_mindmap"] += 1
        return self._mindmap

    def chat_stream(self, text, question, history=None):
        self._calls["chat_stream"] += 1
        yield from self._answer_tokens


class StubExtractor:
    """替换 SubtitleExtractor。``extract`` 也计数，用来证明追问没有重跑字幕提取。"""

    def __init__(self, has_subtitle=True, full_text="transcript"):
        self.has = has_subtitle
        self.full_text = full_text if has_subtitle else ""
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def extract(self, url):
        self._calls += 1
        return {
            "has_subtitle": self.has,
            "full_text": self.full_text,
            "segments": [],
        }


# ── 接缝二：清掉所有线程的连接 ─────────────────────────────

def close_all_thread_connections() -> None:
    """关闭并清除**所有**线程缓存的数据库连接，含线程池里的工作线程。

    ``threading.local()`` 的属性只能由持有它的线程删除，主测试线程拿不到
    工作线程那份。所以这里借 ``database`` 记下的连接清单反查：
    连接是 ``get_db()`` 建的，``database`` 知道它们是哪些。

    工作线程下次再调 ``get_db()`` 时发现连接已关闭，会重新建一份指向
    当前 ``DB_PATH`` 的连接。
    """
    database.forget_all_connections()

    tl = database._thread_local
    if getattr(tl, "conn", None) is not None:
        try:
            tl.conn.close()
        except sqlite3.Error:
            pass
        del tl.conn


# ── 接缝三：真实 HTTP 客户端 ───────────────────────────────

def make_client(app) -> TestClient:
    """构造走真实依赖注入的 TestClient。

    这里**不**覆写 ``get_optional_user`` / ``get_current_user``：
    覆写掉它们就等于又回到「手写依赖返回值」，鉴权行为根本没被执行。
    是否登录由请求头决定——不带 Authorization 即未登录，
    带真实 JWT 即已登录。两者得到不同结果，才证明鉴权真的被执行了。
    """
    return TestClient(app)


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
