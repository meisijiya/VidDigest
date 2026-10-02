"""用户自带凭据（BYOK）：结构上不可打印的封装。

**为什么单独一个模块**，而不把它放进 ``summarizer``：
``api_summarize`` 顶层刻意不 import ``summarizer``（openai / yt_dlp 都很重，
那是为了让路由模块保持廉价）。凭据要在**请求处理**的入口就被包起来，
而入口拿不到 ``summarizer``——把它塞进去等于逼着最热的请求路径把整个
重依赖拖进来。放独立模块，这条既有约定就不用为它让路。
"""


class UserCredential:
    """用户自带凭据。

    ``__repr__`` 与 ``__str__`` 都只会给出遮蔽串，真值在任何打印路径上
    都拿不到——包括 f-string、容器 repr、以及异常回溯里被顺手打印的局部变量。
    这不是「测试保证没人打印」，而是「打印不出来」：测试只能证明现在没写，
    证明不了以后没人写，而只要凭据是 ``str``，那两条路就永远留着。
    """

    # __slots__：不给临时挂一个 ``cred.api_key = ...`` 的余地。
    # 少一个能装真值的属性，少一条「不小心打印」的路径。
    __slots__ = ("_api_key",)

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key or ""

    def __repr__(self) -> str:
        return "<UserCredential redacted>"

    __str__ = __repr__

    def reveal(self) -> str:
        """**唯一**能拿回真值的出口。

        调用点必须少到能逐个检查过——真值一旦散进多个模块，
        「哪里会打印它」就再也数不清了。
        """
        return self._api_key

    @classmethod
    def from_secret(cls, secret) -> "UserCredential | None":
        """从 pydantic 的 ``SecretStr`` 构造；空白等价于「没填」，返回 None。

        **为什么路由不自己取值**：``get_secret_value()`` 返回的是裸字符串，
        路由里一旦写成 ``raw = req.user_api_key.get_secret_value()``，
        就等于在路由的局部变量里放了一条裸串——而路由正是最容易被
        ``logger.debug(f"{req}")`` 顺手扫到的地方，异常回溯也会把它带出去。
        裸串因此只在本模块内部短暂存在，一句就交给 ``__init__``。
        """
        value = secret.get_secret_value() if secret is not None else ""
        return cls(value) if value.strip() else None
