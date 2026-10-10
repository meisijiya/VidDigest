"""`clean_url` 的真实契约：从分享文案里抽出 URL，且**截断在标点前**（工单 #52）。

这个函数存在的**全部理由**就是那件正则里的字符集：

```python
re.search(r"https?://[^\\s）\\)\\"\\'＞，。、；：！？》>\\]]+", self.url)
```

字符集里被排除的那些符号，是「URL 到此为止」的边界。删掉其中任何一批，
正则就退化成 ``[^\\s]+``，而现有测试**照样全绿**——因为唯一一条喂过非裸 URL 的
用例（``test_main_routes.py`` 里那条）URL 后面紧跟的是**半角空格**，
而空格早被 ``\\s`` 排除。这就是工单 #52 的由来。

所以本文件的每一条用例都要求 URL 后面紧跟一个**非空白**的边界符。
只喂空格的那种用例守不住字符集，别再补。

⚠️ **三处复制，只有一处被行为测试钉住。**
``backend/main.py`` 的 ``ParseRequest.clean_url`` 与 ``DownloadRequest.clean_url``
逐字相同（本文件用参数化把两处一起钉住），另有两份前端副本：

  · ``frontend/src/components/HeroSection.vue:215`` — 与后端一致（含 ``\\'``）
  · ``frontend/src/App.vue:389`` — **缺 ``\\'``**

差异的后果：分享文案里 URL 后面紧跟一个半角单引号时，``App.vue`` 抽出的串
会多带上 ``'`` 与其后的文字，后端则正确截断。两份正则由两条不同的前端代码路径
使用，**本单只核实并记录，不改**——改它要先证明哪一份是权威，那是另一张单。

⚠️ **第二条记录：字符集只排除了右/闭形式。** ``）``、``》``、``＞`` 在排除集里，
而对应的左/开形式 ``（``、``《``、``＜`` **不在**。于是
``https://x.com/a/（备注`` 会被整段抽走。本文件不断言这个行为——
断言它等于把一条疑似缺陷钉成契约，而将来修它不需要动任何测试。
"""
import pytest

import main as main_module

#: 字符集里排除的**中文与全角**符号。少一个，下面那条变异就少杀一个分支。
CJK_BOUNDARIES = "，。、；：！？）》＞"

#: 半角边界。`'` 与 `"` 单独在列，因为 `HeroSection.vue` 与后端包含它们，
#: 而 `App.vue` 那份不含——见文件头。
ASCII_BOUNDARIES = ")\"'>]"

#: ⚠️ 字符集只排除了**右/闭**形式，**左/开**形式（`（`、`《`、`＜`）没排。
#: 所以 `https://x.com/a/（备注` 会被整段抽走——`（` 及其后的字都算 URL。
#: 本文件**不断言**这个行为：断言它等于把一条疑似缺陷钉成契约，
#: 而修它不需要改任何测试（见文件头「只核实并记录」）。
#: 排除集是刻意挑的：**只取字符集真的排除的那些**，多加一个就会红，
#: 那红是这条用例写错了，不是代码错了。


def _clean(model_cls, text):
    """按模型形状取出 clean_url 结果。

    `ParseRequest` 与 `DownloadRequest` 的字段名都是 `url`，但两个模型
    各自带一份实现——参数化就是为了让两处一起被钉住。
    """
    return model_cls(url=text).clean_url()


@pytest.fixture(params=[main_module.ParseRequest, main_module.DownloadRequest],
                ids=["ParseRequest", "DownloadRequest"])
def model(request):
    return request.param


class TestTruncatesAtPunctuation:
    @pytest.mark.parametrize("boundary", list(CJK_BOUNDARIES))
    def test_cjk_punctuation_ends_the_url(self, model, boundary):
        """URL 后紧跟中文标点时，标点及其后内容**不**属于 URL。

        这一族用例是字符集的判别力所在：把它们换成半角空格，全部照样通过。
        """
        assert _clean(model, f"【标题】https://v.douyin.com/abc/{boundary}快来看") \
            == "https://v.douyin.com/abc/"

    @pytest.mark.parametrize("boundary", list(ASCII_BOUNDARIES))
    def test_ascii_punctuation_ends_the_url(self, model, boundary):
        assert _clean(model, f"https://v.douyin.com/abc/{boundary}tail") \
            == "https://v.douyin.com/abc/"

    def test_whitespace_ends_the_url(self):
        """空格那条本来就成立（``\\s`` 一直在字符集里）。

        留着它是为了对照：正因如此，光有这一族用例守不住中文标点。
        """
        assert _clean(main_module.ParseRequest,
                      "复制打开抖音 https://v.douyin.com/abc123/ 看看这个视频") \
            == "https://v.douyin.com/abc123/"

    def test_keeps_the_full_url_when_nothing_follows_it(self):
        """URL 在结尾、后面什么都没有时不能被削短。"""
        assert _clean(main_module.ParseRequest, "https://v.douyin.com/abc123/") \
            == "https://v.douyin.com/abc123/"

    def test_takes_the_first_url_not_the_longest(self):
        """抽**第一个** URL，不是最长的那个。"""
        text = "第一个 https://a.example.com/1 第二个 https://b.example.com/222222"
        assert _clean(main_module.ParseRequest, text) == "https://a.example.com/1"


class TestFallbackWhenNoUrl:
    def test_returns_stripped_input_when_nothing_matches(self):
        """抽不到 URL 时退化为 ``strip()`` —— 这条分支此前零覆盖。

        注意它**不做任何校验**：不是 URL 的字符串原样返回。
        所以路由层拿到的是一个用户没打算提交的地址，错误会在更下游才现形。
        """
        assert _clean(main_module.ParseRequest, "  没有链接  ") == "没有链接"

    def test_fallback_still_strips_both_ends(self):
        assert _clean(main_module.ParseRequest, "\t 没有链接 \n") == "没有链接"

    def test_ftp_url_is_not_recognised(self):
        """只认 ``http(s)://``。ftp:// 走退化分支，原样返回。"""
        assert _clean(main_module.ParseRequest, "ftp://files.example.com/x") \
            == "ftp://files.example.com/x"