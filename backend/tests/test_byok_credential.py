"""工单 #9 第一部分：凭据的**结构性遮蔽**（不涉及 HTTP）。

票面要的不只是「现在没打印」，而是「打印不出来」：只要凭据是 ``str``，
f-string / 容器 repr / 异常回溯里被顺手打印的局部变量，任何一条都能把它带出去，
而测试只能证明当前这几行没写，证明不了以后没人写。所以真正的防线是
``UserCredential`` 这个类——它唯一的字符串形式就是遮蔽串。

这批测试能守住的：任何**拼法**都拿不到真值（下面三种各试一遍，
外加 str/repr 直接调用），以及 ``reveal()`` 确实拿得回真值
（否则「不可打印」就成了「根本用不了」的假安全）。
守不住的：将来有人绕过封装、拿环境变量里的字符串直接拼——那不是这条类契约管的事。

哨兵只出现一次、且绝不合法（不是任何服务商会签发的形态），
所以在任何输出里搜到它，就一定是我们放进去的那一个，不可能是巧合。
"""

import pytest

from credentials import UserCredential

#: 只在这个文件里出现的哨兵。真实 key 形态是 sk- + 字母数字，
#: 这里带下划线与大写段，任何签发方都不会产出它。
SENTINEL = "sk-agent-bytok-9f3c1a7e-ZZUNIQUEZZ"

MASK = "<UserCredential redacted>"


def rendered(cred):
    """把凭据放进各种会「顺手打印」它的容器与模板里。

    三种拼法是票面点名的：f-string、列表 repr、字典 repr（容器会走 repr）。
    少一种就少一条可能漏的路径——异常回溯打印局部变量走的是同一套 repr 协议。
    """
    return {
        "fstring": f"{cred}",
        "list": [cred],
        "dict": {cred: 1},
    }


class TestMasked:
    def test_str_and_repr_are_the_same_mask(self):
        cred = UserCredential(SENTINEL)
        assert str(cred) == MASK
        assert repr(cred) == MASK
        assert str(cred) == repr(cred), "str 与 repr 不一致 = 有一条路径能打印出别的东西"

    @pytest.mark.parametrize("spelling", ["fstring", "list", "dict"])
    def test_no_spelling_prints_the_key(self, spelling):
        cred = UserCredential(SENTINEL)
        rendered_text = str(rendered(cred)[spelling])
        assert SENTINEL not in rendered_text, f"{spelling} 这条拼法把真值打印出来了"
        assert MASK in rendered_text, f"{spelling} 这条拼法连遮蔽串都没有，遮蔽本身失效了"

    def test_nested_in_two_containers(self):
        """嵌套容器：repr 是递归的，只测一层证明不了递归里也遮蔽。"""
        cred = UserCredential(SENTINEL)
        blob = repr({"a": [cred], "b": (cred,), "c": {cred: cred}})
        assert SENTINEL not in blob

    def test_percent_format_goes_through_str(self):
        """%s / %r 两条老式格式化同样必须遮蔽。"""
        cred = UserCredential(SENTINEL)
        assert SENTINEL not in ("%s" % cred)
        assert SENTINEL not in ("%r" % cred)

    def test_empty_key_is_still_a_credential(self):
        """空串也包成对象：否则「有无凭据」会用真假值判断，漏掉空串这一路。"""
        assert str(UserCredential("")) == MASK


class TestReveal:
    def test_reveal_returns_the_original(self):
        assert UserCredential(SENTINEL).reveal() == SENTINEL

    def test_reveal_of_empty_is_empty(self):
        assert UserCredential("").reveal() == ""

    def test_no_public_attribute_holds_the_key(self):
        """真值只能藏在 _api_key 里。

        多一个 ``cred.api_key`` 就多一条「不小心打印」的路径——
        属性名不需要调 reveal 就能读到，遮蔽对它无效。
        """
        cred = UserCredential(SENTINEL)
        public = [a for a in dir(cred) if not a.startswith("_")]
        assert "api_key" not in public, f"凭据上有公开属性直接暴露真值：{public}"
        assert not hasattr(cred, "__dict__"), "__dict__ 能被临时挂上任意属性"
