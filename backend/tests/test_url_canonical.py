"""`url_canonical.canonical_video_url` 的行为判据（工单 #25 第一步）。

## 这个文件要证明的事只有一句

**同一个视频，用户正常会粘到的那几种形态，归一之后是同一个字符串。**

形态全部来自工单 #25 的实测矩阵，不是凭印象编的。所以这里最要紧的不是
「某个 URL 变成某个 URL」，而是**矩阵里那一列 N 个形态，收敛成 1 个**。

## 判据按「会不会误伤」排优先级

未识别的链接**必须原样返回**比「多归一几个平台」更要紧：把两个不同的视频
归一成同一个字符串，会让社区里塌成一行，而**不报错**。
所以「不改」这件事要单独钉死，且带阳性对照。

## 命名沿用本仓既有约定

函数名是 ASCII snake_case，中文说明写在 docstring 里。
（Python 标识符不允许全角括号与逗号；且本仓既有测试全是英文函数名。）
"""
import pytest

from url_canonical import canonical_video_url as c

# ── 形态矩阵：一个视频 N 种形态，必须收敛成 1 个 ─────────────────

#: 同一个 B 站视频 BV1aa411c7mD 的 8 种形态，全部取自工单 #25 的实测构造。
BILIBILI_FORMS = [
    "https://www.bilibili.com/video/BV1aa411c7mD",
    "https://bilibili.com/video/BV1aa411c7mD",
    "https://m.bilibili.com/video/BV1aa411c7mD",
    "https://www.bilibili.com/video/BV1aa411c7mD?spm_id_from=333.1007.tianma",
    "https://www.bilibili.com/video/BV1aa411c7mD/?vd_source=abc",
    "https://www.bilibili.com/video/BV1aa411c7mD/",
    # 真实分享文案里 clean_url() 抽出来的形态：末尾一个**裸 ?**
    "https://www.bilibili.com/video/BV1aa411c7mD?",
    "https://www.bilibili.com/video/BV1aa411c7mD?spm",
]

YOUTUBE_FORMS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
    "https://youtu.be/dQw4w9WgXcQ",
]

DOUYIN_FORMS = [
    "https://www.douyin.com/video/7234567890123456789",
    "https://www.iesdouyin.com/share/video/7234567890123456789",
]


@pytest.mark.parametrize("form", BILIBILI_FORMS)
def test_bilibili_every_form_maps_to_one_value(form):
    """B 站：用户正常会粘的 8 种形态，都归到同一个值。"""
    assert c(form) == "https://www.bilibili.com/video/BV1aa411c7mD"


def test_bilibili_eight_forms_collapse_to_one():
    """矩阵那一列是结论，不是逐条断言的副产品。

    逐条断言只能证明「每种形态各自对」，证明不了「它们彼此相同」。
    用集合的基数把这件事钉死——去掉任意一条规则，这个数立刻变大。
    """
    assert len({c(f) for f in BILIBILI_FORMS}) == 1


@pytest.mark.parametrize("form", YOUTUBE_FORMS)
def test_youtube_every_form_maps_to_one_value(form):
    """YouTube：watch?v= 与 youtu.be/ 是同一条。"""
    assert c(form) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_four_forms_collapse_to_one():
    assert len({c(f) for f in YOUTUBE_FORMS}) == 1


@pytest.mark.parametrize("form", DOUYIN_FORMS)
def test_douyin_every_form_maps_to_one_value(form):
    assert c(form) == "https://www.douyin.com/video/7234567890123456789"


def test_douyin_two_forms_collapse_to_one():
    assert len({c(f) for f in DOUYIN_FORMS}) == 1


# ── 不同视频绝不能塌成一行 ─────────────────────────────────────

def test_different_bv_ids_do_not_collapse():
    """与上面那组成对读。

    少了这条，「归一」就可能退化成「按平台取前 N 位」那种一切皆可的实现，
    而那种实现**不报错**，只是让社区慢慢塌。
    """
    assert c("https://www.bilibili.com/video/BV1aa411c7mD") != c(
        "https://www.bilibili.com/video/BV1zz411c7mX"
    )


def test_different_youtube_ids_do_not_collapse():
    assert c("https://youtu.be/dQw4w9WgXcQ") != c("https://youtu.be/aBcDeFgHiJk")


def test_same_id_on_different_platforms_does_not_collapse():
    """阳性对照：别把 B 站与 YouTube 的规则写串了。"""
    assert c("https://www.bilibili.com/video/BV1aa411c7mD") != c(
        "https://www.youtube.com/watch?v=BV1aa411c7mD"
    )


# ── 未识别的链接必须原样返回 ───────────────────────────────────
#
# 这一组比上面都重要。归一错了的代价是「两个不同的视频塌成一行」，
# 而且**不报错**。所以「不改」要单独钉，且带阳性对照。

@pytest.mark.parametrize("url", [
    # 短链：id 不在 URL 里，本工单明确不处理（见模块 docstring）
    "https://b23.tv/AbCdEf",
    "https://xhslink.com/aBcDeF",
    # 小红书：只有 www + 24 位 hex 那一种形态
    "https://www.xiaohongshu.com/explore/0123456789abcdef01234567",
    # 完全不认识的主机
    "https://v.example/watch/abc",
    "not a url at all",
    "",
])
def test_unrecognised_url_is_returned_verbatim(url):
    """未识别的一律原样返回——绝不做「猜一个规范值」的改写。"""
    assert c(url) == url


def test_recognised_url_is_actually_changed():
    """阳性对照：证明上面那组不是因为「函数什么都不做」而绿。"""
    assert c("https://m.bilibili.com/video/BV1aa411c7mD") != (
        "https://m.bilibili.com/video/BV1aa411c7mD"
    )


def test_iqiyi_keeps_its_html_suffix():
    """爱奇艺的 `.html` 不能抹掉。

    AGENTS.md 实测：不带 `.html` 不掉进 iqiyi extractor、掉进 `[generic]` 兜底。
    也就是说把 `.html` 抹掉等于把它变成另一个东西——那不是去重，是改语义。
    """
    with_html = c("https://www.iqiyi.com/v_19rr7depxo.html")
    without = c("https://www.iqiyi.com/v_19rr7depxo")
    assert with_html == "https://www.iqiyi.com/v_19rr7depxo.html"
    assert without == "https://www.iqiyi.com/v_19rr7depxo"


def test_bilibili_collection_page_is_not_canonicalised():
    """`/list/` 是合集页不是单条视频。

    归一合集页会把整个合集塌成一行，而合集里的每条视频在库里本该各自独立。
    """
    url = "https://www.bilibili.com/list/1234567890"
    assert c(url) == url


def test_a_longer_path_is_not_truncated_to_the_bv_id():
    """`/video/<BV>/<别的东西>` **不是**那条视频。

    这是一条实测出来的漏洞，不是假想：不锚定结尾时
    `.../video/BV1TEST/a` 与 `.../video/BV1TEST/b` 都会被截成 `BV1TEST`，
    两个**不同的**视频塌成同一个 canonical。而第 3 片建上唯一索引之后，
    它立刻变成「后写的那个被当成同一个视频而合并掉」——
    实测由 `test_history_search_favorites.py` 的
    `test_facets_are_not_narrowed_by_the_current_filters` 抓到（那里恰好
    用了 `https://www.bilibili.com/video/BV1TEST` 拼 `/a`、`/b` 造两条记录）。

    本仓自己的测试数据都能触发它，这足以说明它不是假想。
    """
    assert c("https://www.bilibili.com/video/BV1TEST/a") == (
        "https://www.bilibili.com/video/BV1TEST/a"
    )
    assert c("https://www.bilibili.com/video/BV1TEST/a") != c(
        "https://www.bilibili.com/video/BV1TEST/b"
    )


def test_a_longer_douyin_path_is_not_truncated_to_the_id():
    """抖音同理要锚定：`/video/123/a` 不是一条视频。"""
    assert c("https://www.douyin.com/video/7234567890123456789/a") == (
        "https://www.douyin.com/video/7234567890123456789/a"
    )
    assert c("https://www.douyin.com/video/7234567890123456789/a") != c(
        "https://www.douyin.com/video/7234567890123456789/b"
    )


def test_youtube_watch_without_v_param_is_left_alone():
    """没有 `v=` 的 watch 页不是一条具体视频，原样返回。"""
    url = "https://www.youtube.com/watch?list=PL1234567890"
    assert c(url) == url


# ── 不变量 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("form", BILIBILI_FORMS + YOUTUBE_FORMS + DOUYIN_FORMS)
def test_canonicalisation_is_idempotent(form):
    once = c(form)
    assert c(once) == once


def test_canonical_value_is_still_usable_by_the_platform():
    """归一值会进库、会被回显，它必须仍是一个 yt-dlp 认得的地址。

    否则「去重」就是以「解析不出来」为代价换来的。
    """
    assert c("https://m.bilibili.com/video/BV1aa411c7mD").startswith(
        "https://www.bilibili.com/video/"
    )
    assert c("https://youtu.be/dQw4w9WgXcQ").startswith(
        "https://www.youtube.com/watch?v="
    )


def test_signature_params_survive_on_unrecognised_platforms():
    """签名泄漏是工单 #25 最初要解决的问题。

    这里**刻意**不归一未知平台，所以签名原样保留——泄漏面没有被本工单悄悄扩大。
    真要闭掉它需要单独决定「回显哪个版本」，那是票面的第 2 问。
    """
    url = "https://v.example/x?sig=SECRET&exp=1"
    assert "sig=SECRET" in c(url)