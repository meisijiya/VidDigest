"""用户自带凭据（BYOK）：结构上不可打印的封装。

**为什么单独一个模块**，而不把它放进 ``summarizer``：
``api_summarize`` 顶层刻意不 import ``summarizer``（openai / yt_dlp 都很重，
那是为了让路由模块保持廉价）。凭据要在**请求处理**的入口就被包起来，
而入口拿不到 ``summarizer``——把它塞进去等于逼着最热的请求路径把整个
重依赖拖进来。放独立模块，这条既有约定就不用为它让路。

端点（base_url / model）随凭据一起封装，而不是作为另一对裸字符串在路由里
传来传去：那两条同样是「用户说了算的、会被拼进出网请求」的值，
让它们与 api_key 走同一条路，才能保证「哪条路能拿到真值」只有一个答案。
"""

#: 自定义端点允许的协议。http 是**有意**放行的：自建 / 本地推理服务
#: （Ollama、vLLM、LM Studio）几乎都只监听 http，强制 HTTPS 等于把
#: 最需要 BYOK 的那批用户挡在门外。真正的风险不在明文传输上——
#: 请求体里的凭据只流向用户自己指定的那台机器。
ALLOWED_BASE_URL_SCHEMES = ("http", "https")

#: 端点长度上限。不是安全边界，是「有人把整段请求体塞进这个字段」时
#: 顺手拦一下，免得它被原样拼进出网请求。
MAX_BASE_URL_LENGTH = 500
MAX_MODEL_LENGTH = 200


class CredentialError(ValueError):
    """凭据本身不合法。

    单独一个类型而不是复用 ValueError：调用方要靠它把「你填错了」
    与「服务端配置有问题」分开处理——前者是用户的 4xx，后者是 5xx。
    消息里**永远不含**用户填的原文（见 ``validate_base_url``）。
    """


def validate_base_url(raw: str | None) -> str:
    """把用户填的端点收敛成可出网的地址，空白返回 ""。

    拒绝的三件事，逐条都有具体后果：

    1. **非 http/https 的协议**（``file:`` / ``gopher:`` / ``ftp:``）。
       OpenAI SDK 会照着它拼请求，落到本地文件或内网协议上。
    2. **内嵌 userinfo**（``https://sk-xxx@host/``）。两重危害：它本身
       就是一条把凭据塞进 URL 的路径（会进日志、进代理），而带 userinfo
       的地址在不同客户端下还会触发 Basic 认证，与 Authorization 头打架。
    3. **没有 host**（``https:///v1``）。

    错误消息是**固定文案**，不回显用户填的原文：原值可能本身就是
    一个被人误粘进来的密钥，抄进响应就等于把它送回浏览器与代理日志。
    """
    from urllib.parse import urlsplit

    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) > MAX_BASE_URL_LENGTH:
        raise CredentialError("端点地址过长")

    try:
        parts = urlsplit(text)
    except ValueError:
        raise CredentialError("端点地址格式不对") from None

    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_BASE_URL_SCHEMES:
        raise CredentialError("端点地址只支持 http 或 https")
    if parts.username or parts.password or "@" in parts.netloc:
        raise CredentialError("端点地址里不能带用户名或密码")
    if not parts.hostname:
        raise CredentialError("端点地址缺少主机名")
    return text.rstrip("/")


def validate_model(raw: str | None) -> str:
    """模型名。空白返回 ""，表示「用平台默认的」。

    只做长度与字符集检查：模型名由用户所选的服务商解释，
    服务端无权替他判断哪个模型存在——猜错一次就会把「模型名写错」
    报成「凭据无效」，指向完全错误的方向。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) > MAX_MODEL_LENGTH:
        raise CredentialError("模型名过长")
    # 控制字符能进日志、能拼进 header，挡在这里
    if any(ch < " " or ch == "\x7f" for ch in text):
        raise CredentialError("模型名含有非法字符")
    return text


class UserCredential:
    """用户自带凭据 + 可选的端点覆盖。

    ``__repr__`` 与 ``__str__`` 都只会给出**恒定**的遮蔽串，真值在任何打印
    路径上都拿不到——包括 f-string、容器 repr、以及异常回溯里被顺手打印的
    局部变量。这不是「测试保证没人打印」，而是「打印不出来」：测试只能证明
    现在没写，证明不了以后没人写，而只要凭据是 ``str``，那两条路就永远留着。

    **端点也不进 repr**，尽管它本身不是秘密。理由是它是**用户可控的任意
    字符串**：把凭据写进 query（``https://host/v1?key=sk-xxx``）比写进
    userinfo 更常见，而我们只拒绝 userinfo。一旦端点进了 repr，
    「任何打印路径都拿不到用户填的东西」这条保证就没了——而那正是这个类
    存在的全部理由。诊断端点问题请走 ``endpoint`` 属性并只打给本人看。
    """

    # __slots__：不给临时挂一个 ``cred.api_key = ...`` 的余地。
    # 少一个能装真值的属性，少一条「不小心打印」的路径。
    __slots__ = ("_api_key", "_base_url", "_model")

    #: repr / str 的唯一取值。提为类常量是为了让「恒定」这件事
    #: 在代码里可核对，而不是靠读者相信。
    MASK = "<UserCredential redacted>"

    def __init__(self, api_key: str, base_url: str = "", model: str = "") -> None:
        self._api_key = api_key or ""
        # 校验放在构造里而不是调用点：调用点有五处（chat / summarize /
        # 覆盖 / …），漏一处就是一条能把出网地址带歪的路径。
        self._base_url = validate_base_url(base_url)
        self._model = validate_model(model)

    def __repr__(self) -> str:
        return self.MASK

    __str__ = __repr__

    def reveal(self) -> str:
        """**唯一**能拿回凭据真值的出口。

        调用点必须少到能逐个检查过——真值一旦散进多个模块，
        「哪里会打印它」就再也数不清了。
        """
        return self._api_key

    @property
    def endpoint(self) -> tuple[str, str]:
        """(base_url, model)。任一为空表示「这一项走平台默认」。

        返回 tuple 而不是两个方法：这两个值**总是**一起用，
        分成两个出口就多一种「只拿到一半」的组合。
        """
        return self._base_url, self._model

    @classmethod
    def from_secret(cls, secret, base_url: str = "", model: str = "") -> "UserCredential | None":
        """从 pydantic 的 ``SecretStr`` 构造；**凭据**空白等价于「没填」，返回 None。

        注意判空只看 api_key，不看端点：填了端点却没填 key 是配不起来的，
        当成「没填」比当成「配错了」对用户更诚实。

        **为什么路由不自己取值**：``get_secret_value()`` 返回的是裸字符串，
        路由里一旦写成 ``raw = req.user_api_key.get_secret_value()``，
        就等于在路由的局部变量里放了一条裸串——而路由正是最容易被
        ``logger.debug(f"{req}")`` 顺手扫到的地方，异常回溯也会把它带出去。
        裸串因此只在本模块内部短暂存在，一句就交给 ``__init__``。
        """
        value = secret.get_secret_value() if secret is not None else ""
        return cls(value, base_url, model) if value.strip() else None
