"""视频 format 字典的**单一出处**（工单 #44）。

背景
----
这个字典原先在四处各写一遍 14 行的字面量（`downloader.py` 两处、
`douyin.py` 两处，外加 `downloader.py` 一处 `{**best_video}` 派生）。
每处漏写一个键 = 那个平台上少一个字段，而症状是界面渲染出 `undefined`
**且不报错**。工单 #43 先给它加了形状守卫，本单用它当安全网做收口。

为什么单独一个模块
------------------
对标已有的 `tags.py`（`MAX_TAGS` / `TAG_VOCABULARY` / `validate_tags`
都在那里）——单一职责的领域小模块是本仓既有的写法。
放进 `downloader.py` 会让 `douyin.py` 反向依赖下载器；放进 `douyin.py`
则是让通用下载路径依赖一个平台专用模块。两个方向都是错的。

**未知键抛错，而不是静默新增**
--------------------------------
这才是收口的真正收益。原先写错一个键名，字典里就多出一个**没有任何人读的
字段**，而所有形状检查都还绿。`make_format(filesize_apporx=1)` 会当场
报错——把「打错字」从静默变成响亮。
"""

#: format 字典的键集合与中性默认值。**这是唯一一处定义**。
#:
#: 为什么默认值取中性值而不是「各站点的常见值」：调用方少写一个键时，
#: 拿到**默认值**总比**缺键**好——缺键在前端是 `undefined`，默认值是
#: 「这一项确实没有」，两者在语义上不是一回事。
FORMAT_DEFAULTS = {
    "format_id": "",
    "ext": "",
    "resolution": "",
    "height": 0,
    "width": 0,
    "filesize": None,
    "filesize_approx": None,
    "vcodec": None,
    "acodec": None,
    "abr": None,
    "has_video": False,
    "has_audio": False,
    "kind": "video",
    "label": "",
}


def make_format(**overrides) -> dict:
    """造一个 format 字典：键集合固定，值由调用方覆盖。

    未知键**抛 KeyError**而不是静默塞进去——见模块 docstring。

    >>> make_format(format_id="x", kind="audio")["has_audio"]
    False
    >>> make_format(nope=1)                                   # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
    KeyError: ...
    """
    unknown = sorted(set(overrides) - set(FORMAT_DEFAULTS))
    if unknown:
        raise KeyError(
            f"format 字典没有这些键：{unknown}；"
            f"可用的键是 {sorted(FORMAT_DEFAULTS)}。"
            "加键要改 FORMAT_DEFAULTS（那才是单一出处），"
            "不要在这里绕过。"
        )
    return {**FORMAT_DEFAULTS, **overrides}