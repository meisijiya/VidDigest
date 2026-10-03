"""提示词模板加载器（prompt_template.py）与 prompts/*.md 的约定。

这个模块存在的理由是「让改提示词不必改 Python」。它本身要被测的不是
「渲染出来对不对」——那由 test_summarize_routes 的内容要求守着——
而是几条**只有加载器会犯**的错：

1. 头部注释泄漏进提示词。模型先读到一段讲「$sentinel 是什么」的话，
   既烧 token，也可能把占位符名字当成要输出的内容。
2. 占位符对不上时**静默**通过。一份带着 ``$subtitle`` 字面量发出去的
   提示词不会报错，它只会安静地少掉整段字幕，然后产出一份凭空编的总结。
3. 按 CWD 找文件。生产环境 uvicorn 的工作目录未必是 backend/，
   按 CWD 找会得到一句没人查得到原因的「不存在」。
"""
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import prompt_template  # noqa: E402
import summarizer  # noqa: E402
import tags  # noqa: E402

VALUES = {
    "lang_hint": "中文",
    "vocabulary": "词表正文",
    "sentinel": summarizer.MINDMAP_TAGS_SENTINEL,
    "subtitle": "字幕正文",
}


def render(**overrides):
    return prompt_template.render_prompt("summarize_full", {**VALUES, **overrides})


class TestTemplateLoads:
    def test_the_shipped_template_renders(self):
        out = render()
        assert "字幕正文" in out
        assert "词表正文" in out
        assert summarizer.MINDMAP_TAGS_SENTINEL in out
        assert "中文" in out

    def test_it_is_found_regardless_of_the_working_directory(self, tmp_path, monkeypatch):
        """按 CWD 找模板是最容易犯、也最难查的一种错。

        症状是「本地好好的，一部署就提示词文件不存在」——
        而 uvicorn 的工作目录没人会去核对。
        """
        monkeypatch.chdir(tmp_path)
        assert "字幕正文" in render()

    def test_the_prompt_is_not_hardcoded_in_python_anymore(self):
        """源码里不该再有那三行特征文字。

        这条守的是「有人图省事把提示词又搬回 f-string」——
        那不会让任何别的测试变红，只会让「改提示词不用改代码」悄悄失效。
        """
        src = (BACKEND / "summarizer.py").read_text(encoding="utf-8")
        body = src.split("def _build_full_prompt")[1].split("def _build_chat_prompt")[0]
        assert "【第一部分：总结正文】" not in body, "提示词又回到代码里了"
        assert "【第二部分：思维导图】" not in body
        assert "render_prompt(" in body, "应该在渲染模板"

    def test_the_md_file_is_actually_on_disk(self):
        path = prompt_template.prompt_path("summarize_full")
        assert path.is_file(), f"{path} 不存在：改提示词的人按文档找不到它"
        assert path.suffix == ".md", "文件名要让人一眼看出这是提示词"


class TestCommentsNeverReachTheModel:
    def test_header_comment_is_stripped(self):
        out = render()
        assert "<!--" not in out
        assert "-->" not in out

    def test_stripping_keeps_everything_after_the_comment(self):
        """剥离必须是非贪婪的。

        写成贪婪的话，第一个 --> 之后到文件尾的**全部提示词**都会被吞掉，
        而剩下的部分恰好还是一段合法提示词——模型照收不误，
        只是从此不知道该怎么输出了。
        """
        out = render()
        for marker in ("【第一部分", "【第二部分", "【第三部分", "【输出格式", "视频字幕内容："):
            assert marker in out, f"注释剥离吞掉了「{marker}」及其后的内容"


class TestPlaceholderMismatchesAreLoud:
    def test_a_missing_value_raises_rather_than_leaving_the_name(self):
        bad = {k: v for k, v in VALUES.items() if k != "subtitle"}
        with pytest.raises(prompt_template.PromptError) as ei:
            prompt_template.render_prompt("summarize_full", bad)
        assert "subtitle" in str(ei.value)

    def test_the_error_names_the_template(self):
        with pytest.raises(prompt_template.PromptError) as ei:
            prompt_template.render_prompt("summarize_full", {})
        assert "summarize_full" in str(ei.value), (
            "报错不说是哪个模板，改提示词的人得自己一个个试"
        )

    def test_an_unknown_template_says_where_it_looked(self):
        with pytest.raises(prompt_template.PromptError) as ei:
            prompt_template.render_prompt("nope_not_here", VALUES)
        assert "nope_not_here" in str(ei.value)

    def test_a_traversal_attempt_is_refused(self):
        """模板内容直接进模型，所以「从哪读」不能由调用点随便决定。"""
        for bad in ("../secrets", "a/b", "..\\windows", ".hidden"):
            with pytest.raises(prompt_template.PromptError):
                prompt_template.prompt_path(bad)

    def test_a_bare_dollar_is_an_error_not_a_silent_drop(self):
        """字面量 $ 必须写成 $$。

        静默吞掉的话，用户会看到自己的提示词里少了一段，
        而且没有任何线索指向「你少写了一个 $」。
        """
        p = prompt_template.PROMPTS_DIR / "_tmp_dollar.md"
        p.write_text("价格是 $100 与 $200", encoding="utf-8")
        try:
            with pytest.raises(prompt_template.PromptError) as ei:
                prompt_template.render_prompt("_tmp_dollar", {})
            assert "$$" in str(ei.value), "报错没告诉用户该怎么写"
        finally:
            p.unlink()
            prompt_template.load_template.cache_clear()

    def test_escaped_dollar_renders_as_a_literal(self):
        p = prompt_template.PROMPTS_DIR / "_tmp_dollar2.md"
        p.write_text("价格是 $$100，字幕=$subtitle", encoding="utf-8")
        try:
            out = prompt_template.render_prompt("_tmp_dollar2", {"subtitle": "X"})
            assert out == "价格是 $100，字幕=X", repr(out)
        finally:
            p.unlink()
            prompt_template.load_template.cache_clear()


class TestTemplateContentContract:
    """模板里**不能少**的东西。

    这些断言以前长在 test_summarize_routes 里。它们守的东西没变，
    只是模板换了地方——所以这里断言的是**渲染后**的提示词，
    与那边同源，避免两处对「提示词里该有什么」有各自的理解。
    """

    def test_sentinel_reaches_the_model(self):
        """哨兵没了，模型就只吐总结，导图与标签整个消失——且不报错。"""
        assert summarizer.MINDMAP_TAGS_SENTINEL in render()

    def test_vocabulary_reaches_the_model(self):
        """词表没了，模型只能自创标签（服务端会拦下，于是标签全丢）。"""
        assert tags.vocabulary_prompt_text() in render(vocabulary=tags.vocabulary_prompt_text())

    def test_json_braces_need_no_escaping(self):
        """模板里的花括号是提示词的一部分，不该出现双写。

        用 str.format 的话作者必须写 {{ }}，于是「删掉一对括号」这种
        看起来无害的编辑会静默改变输出给模型的 JSON 形状。
        """
        out = render()
        assert '"mindmap"' in out
        assert '{{' not in out and '}}' not in out, "模板里出现了转义花括号"

    def test_language_hint_is_interpolated_not_literal(self):
        out = render(lang_hint="与原文相同的语言")
        assert "使用与原文相同的语言输出" in out
        assert "$lang_hint" not in out

    def test_subtitle_is_truncated_by_the_caller_not_the_template(self):
        """截断逻辑留在 Python。

        放进模板的话，「截到多少字」这个决定就散到了两个地方，
        而改提示词的人看不到它。

        判据取**结尾**而不是全文计数：模板正文里本来就有 13 个「字」
        （字数、十字……），数全文会把它们算进去。
        """
        long_sub = "Ω" * 20000
        out = summarizer.VideoSummarizer._build_full_prompt(long_sub, "zh")
        assert out.endswith("Ω" * 15000), (
            f"结尾不是 15000 个 Ω，实际尾部是 {out[-20:]!r}"
        )
        assert "Ω" * 15001 not in out, "截断没生效"
