"""summarizer 里三个**纯函数**的契约（工单 #55）。

``_parse_vtt`` / ``_time_to_seconds`` / ``clean_mindmap_markdown`` 此前零断言，
根因是它们在测试里的唯一入口 ``_download_and_parse_via_ytdlp`` 被
``test_subtitle_priority.py:139-141`` 桩掉了。

同一个文件里已经有正确做法可抄：``test_subtitle_priority.py`` **故意不桩**
``_pick_best_subtitle``，注释写着「把它也换成桩就等于把被测的东西一起换掉了」。
本文件照它办——直接喂 VTT 文本 / Markdown 文本，不经任何下载入口。

它们纯在哪：输入输出确定，不出网。写错时的症状是「字幕时间轴整体错位」
与「思维导图渲染成一坨」，从界面反推极难，所以值得在纯函数这一层钉住。
"""
import pytest

from summarizer import (
    SubtitleExtractor, VideoSummarizer, _time_to_seconds,
)

#: ``clean_mindmap_markdown`` 是 ``VideoSummarizer`` 上的 staticmethod，
#: 不是模块级函数 —— 它住在 ``VideoSummarizer`` 里（summarizer.py:906）。
clean_mindmap_markdown = VideoSummarizer.clean_mindmap_markdown


# ── _time_to_seconds ──────────────────────────────────────────


class TestTimeToSeconds:
    @pytest.mark.parametrize("ts,expected", [
        ("00:00:00", 0.0),
        ("00:00:01", 1.0),
        ("00:00:01.500", 1.5),
        ("00:01:30", 90.0),
        ("01:00:00", 3600.0),
        ("01:02:03.004", 3723.004),
    ])
    def test_hms_form(self, ts, expected):
        assert _time_to_seconds(ts) == pytest.approx(expected)

    def test_comma_is_accepted_as_the_decimal_separator(self):
        """SRT 用逗号，VTT 用点。两种都要吃得下。"""
        assert _time_to_seconds("00:00:01,500") == pytest.approx(1.5)

    def test_two_part_form_is_minutes_and_seconds(self):
        assert _time_to_seconds("01:30") == pytest.approx(90.0)

    def test_bare_number_is_seconds(self):
        assert _time_to_seconds("42") == pytest.approx(42.0)


# ── _parse_vtt ────────────────────────────────────────────────


VTT_STANDARD = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
第一句话

2
00:00:05.500 --> 00:00:08.250
第二句话
"""


def _write_vtt(tmp_path, text, name="sub.vtt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestParseVtt:
    def test_parses_a_standard_file(self, tmp_path):
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, VTT_STANDARD))
        assert [s["text"] for s in segs] == ["第一句话", "第二句话"]
        assert segs[0]["start"] == pytest.approx(1.0)
        assert segs[0]["end"] == pytest.approx(4.0)
        assert segs[1]["start"] == pytest.approx(5.5)
        assert segs[1]["end"] == pytest.approx(8.25)

    def test_accepts_a_file_without_the_webvtt_header(self, tmp_path):
        """有些平台的字幕没有 ``WEBVTT`` 首行，那不该让整份解析失败。"""
        text = "00:00:01.000 --> 00:00:02.000\n只有一句\n"
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, text))
        assert [s["text"] for s in segs] == ["只有一句"]

    def test_cue_numbers_are_not_mistaken_for_text(self, tmp_path):
        """序号行是数字，必须被当序号而不是正文。

        少了这条，一个把序号也读进 text 的实现照样能用——症状是字幕里
        每段前面挂着一个「1」「2」。
        """
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, VTT_STANDARD))
        assert not any(s["text"].strip().isdigit() for s in segs)

    def test_inline_tags_are_stripped(self, tmp_path):
        text = (
            "00:00:01.000 --> 00:00:02.000\n"
            "带<v Speaker>标签</v>和<c.colorE5E5E5>颜色</c>的一行\n"
        )
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, text))
        assert segs[0]["text"] == "带标签和颜色的一行"

    def test_repeated_text_is_dropped(self, tmp_path):
        """滚动字幕会把同一句话重复几遍，解析结果不该跟着重复。"""
        text = (
            "00:00:01.000 --> 00:00:02.000\n同一句\n\n"
            "00:00:03.000 --> 00:00:04.000\n同一句\n\n"
            "00:00:05.000 --> 00:00:06.000\n另一句\n"
        )
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, text))
        assert [s["text"] for s in segs] == ["同一句", "另一句"]

    def test_multiline_cue_is_joined_into_one_segment(self, tmp_path):
        text = (
            "00:00:01.000 --> 00:00:04.000\n"
            "上半句\n下半句\n"
        )
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, text))
        assert len(segs) == 1
        assert segs[0]["text"] == "上半句 下半句"

    def test_a_file_with_no_cue_yields_no_segments(self, tmp_path):
        """空文件 / 只有头部 → 空列表，不是异常。

        反向对照：上面那些用例断言的是「解析出了东西」，
        少了这条，一个恒返回非空的实现也拦不住。
        """
        assert SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, "WEBVTT\n\n")) == []
        assert SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, "")) == []

    def test_tolerates_extra_blank_lines_between_blocks(self, tmp_path):
        text = VTT_STANDARD.replace("\n\n", "\n\n\n\n")
        segs = SubtitleExtractor._parse_vtt(_write_vtt(tmp_path, text))
        assert [s["text"] for s in segs] == ["第一句话", "第二句话"]


# ── clean_mindmap_markdown ────────────────────────────────────


class TestCleanMindmapMarkdown:
    def test_strips_the_code_fence(self):
        md = "```markdown\n# 主题\n\n- 分支\n```"
        out = clean_mindmap_markdown(md)
        assert out.startswith("# 主题")
        assert "```" not in out

    def test_drops_the_preamble_before_the_first_heading(self):
        """模型常常先写一句「以下是思维导图：」——那句会让 markmap 渲染错位。"""
        out = clean_mindmap_markdown("以下是为你生成的思维导图：\n\n# 主题\n- 分支")
        assert out.startswith("# 主题")
        assert "以下是为你生成的" not in out

    def test_a_single_root_is_enough(self):
        """已经有 ``# `` 一级标题时原样保留，不加第二个根。"""
        out = clean_mindmap_markdown("# 主题\n- 甲\n- 乙")
        assert out == "# 主题\n- 甲\n- 乙"
        assert out.count("\n# ") == 0

    def test_deepest_starting_heading_is_promoted_to_the_root(self):
        """从 ``## `` 开头时提升一级——markmap 需要唯一的 ``# `` 根。"""
        out = clean_mindmap_markdown("## 主题\n- 甲\n- 乙")
        assert out.startswith("# 主题")
        assert "## 主题" not in out

    def test_plain_lines_become_a_root_plus_a_list(self):
        """一个标题都没有时，把非空行转成列表，别返回裸文本。

        markmap 拿到没有根节点的输入时渲染成一坨，而用户看不出原因。
        """
        out = clean_mindmap_markdown("第一点\n- 第二点\n\n第三点")
        assert out.startswith("# ")
        assert "- 第一点" in out
        assert "- 第二点" in out
        assert "- 第三点" in out

    def test_empty_input_stays_empty(self):
        assert clean_mindmap_markdown("") == ""

    def test_garbage_only_input_still_yields_a_renderable_root(self):
        """反向对照：空输入那条之外，**全是非符号**的输入也必须可渲染。"""
        out = clean_mindmap_markdown("。。。！！！\n？？？")
        assert out.startswith("# ")