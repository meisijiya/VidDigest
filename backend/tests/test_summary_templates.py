"""总结提示词模板（工单 #105 / ADR 0026）。

判据只读**外部可观察**的东西：

  · HTTP 响应体：模板清单端点、未知模板 id 的 422
  · **发给模型的提示词**：各模板渲染结果里那句只属于它自己的话
  · 社区视频表那一行的 template 列：经 ``get_video_by_url`` 读回
  · 老库升级：列补上没有、老行有没有被回填

不测私有函数，不断言迁移函数的调用顺序。

## 为什么标记要按「独有的一句」挑

「这份提示词是 brief 那份」这件事有好几种测法：看文件名、看调用顺序、
看参数有没有传下去。三种都测不准——文件名是实现细节，调用顺序不是承诺，
而参数传下去不等于用上了。

所以这里挑的是**每份模板独有的一句话**：渲染结果里有它自己的那句话、
且没有别人的那句话。模板被忽略（永远用 full）时，brief/tutorial/analysis
这三条断言会立刻转红——那正是工单 #105 要防的失效。
"""
import asyncio
import json
import re

import pytest

import api_summarize
import auth
import database
import main as main_module
import prompt_template
import summarizer
import tags
from seams import auth_headers, make_client

URL = "https://example.com/summary-template-check"

#: 每份模板**独有**的一句标记（渲染后仍在正文里）。
#:
#: 挑标准：一句在别的模板里不会出现的话。写在提示词正文里而不是头部
#: 注释里——头部注释会被剥掉，拿它当标记就等于断言「注释没被剥掉」。
#: 这里挑的是四份模板各自的篇幅下限：四句话互不包含，改一份模板的
#: 篇幅也不会撞到另一份的标记。
TEMPLATE_MARKERS = {
    "full": "正文不少于 1500 字",
    "brief": "正文 300-600 字",
    "tutorial": "正文不少于 800 字",
    "analysis": "正文不少于 1000 字",
}

#: 老库（工单 #105 之前）的 videos 表结构：与现行 DDL 逐字相同，
#: 只是没有 template 列。抄在这里而不是临时拼——拼出来的"老库"随时
#: 可能与真的老库差一列，而差的那一列正是这条用例要测的东西。
_LEGACY_VIDEOS_DDL = """
CREATE TABLE videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_url TEXT NOT NULL,
    canonical_url TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    summary_md TEXT DEFAULT '',
    mindmap_md TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    subtitle_text TEXT DEFAULT '',
    parsed_by INTEGER,
    regenerating_by INTEGER,
    regenerating_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


class StubExtractor:
    """有字幕的假提取器。"""

    def extract(self, url):
        return {"has_subtitle": True, "full_text": "字幕正文", "segments": []}


class RecordingSummarizer:
    """只做两件事：吐出三样产出，并**记住这次用的是哪份模板**。

    记住模板是这份桩存在的全部理由：模板有没有真的走到模型，只有
    接缝那头知道。
    """

    def __init__(self):
        self.template = None
        self.calls = 0

    def summarize_full_stream(self, text, language, template=None):
        self.calls += 1
        self.template = template
        yield ("summary", "tok-a")
        yield ("mindmap", "# 主题\n## 章节\n### 要点")
        yield ("tags", ["编程"])


def collect(gen):
    """跑完一个 SSE 异步生成器，返回 [(event, payload)]。"""

    async def _run():
        out = []
        async for e in gen:
            raw = e.raw_data
            out.append((e.event, raw if raw in (None, "", "[DONE]") else json.loads(raw)))
        return out

    return asyncio.run(_run())


def _summarize(uid, template=None, url=URL):
    """跑一次解析。``template=None`` = 请求体里不带这个字段。"""
    req = api_summarize.SummarizeRequest(url=url)
    if template is not None:
        req.summary_template = template
    return collect(api_summarize.summarize_video(req, user={"id": uid}))


@pytest.fixture()
def recorded(monkeypatch, db, make_user):
    """装上会记录模板的桩，返回 (桩, 用户 id)。"""
    s = RecordingSummarizer()
    monkeypatch.setattr(api_summarize, "_get_extractor", lambda: StubExtractor())
    monkeypatch.setattr(api_summarize, "_get_summarizer", lambda: s)
    return s, make_user()


class TestTemplateListEndpoint:
    """``GET /api/summary-templates``：前端下拉框的唯一数据来源。"""

    def test_it_lists_the_templates_without_a_login(self, db):
        client = make_client(main_module.app)
        r = client.get("/api/summary-templates")
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert set(body.keys()) == {"default", "templates"}
        assert body["templates"], "清单是空的，前端下拉框将没有选项"
        ids = [t["id"] for t in body["templates"]]
        assert body["default"] in ids, "默认模板不在清单里"
        for item in body["templates"]:
            assert set(item.keys()) == {"id", "name", "description"}, item
            assert item["id"] and item["name"] and item["description"], item

    def test_it_does_not_leak_the_prompt_file_name(self, db):
        """提示词文件名是仓库内部的事。

        暴露出去就等于让前端依赖「提示词从哪个文件读」——而那正是
        本工单要 freedom 改动的实现细节。
        """
        client = make_client(main_module.app)
        r = client.get("/api/summary-templates")
        assert "summarize_" not in r.text, r.text[:300]

    def test_the_list_does_not_depend_on_any_row_of_any_table(self, db):
        """清单真值在仓库里（prompts/templates.json），不在库里。

        判据：先塞一行 template 是乱码的社区视频，再取一次清单——
        两次必须一模一样。清单要是从库里读的，这行乱码就会漏出去。
        """
        client = make_client(main_module.app)
        before = client.get("/api/summary-templates").json()
        with database.get_db() as c:
            c.execute(
                "INSERT INTO videos (video_url, status, template) VALUES (?, ?, ?)",
                ("https://example.com/junk", "ready", "根本不是模板id"),
            )
        after = client.get("/api/summary-templates").json()
        assert after == before, "模板清单居然受 videos 表里的行影响"


class TestUnknownTemplateIsRejected:
    def test_an_unknown_template_id_is_a_422(self, db, make_user):
        """模板 id 会进提示词文件的名字，拼错了早该在入口被挡住。"""
        client = make_client(main_module.app)
        uid = make_user()
        r = client.post(
            "/api/summarize",
            json={"url": URL, "summary_template": "no-such-template"},
            headers=auth_headers(auth.create_token(uid, "u@example.com")),
        )
        assert r.status_code == 422, f"{r.status_code} {r.text[:200]}"


class TestChosenTemplateReachesTheModel:
    def test_the_chosen_template_is_passed_to_the_model(self, recorded):
        s, uid = recorded
        events = _summarize(uid, template="brief")
        assert [e for e in events if e[0] == "done"], events
        assert s.template == "brief", f"模型拿到的是 {s.template!r}，不是 brief"

    def test_the_used_template_is_stored_on_the_community_row(self, recorded):
        """落库那一列是「这份总结是哪份模板产出的」的唯一记录。

        读走 ``get_video_by_url``（生产读出口），不直接查表。
        """
        s, uid = recorded
        _summarize(uid, template="brief")
        row = database.get_video_by_url(URL)
        assert row is not None, "社区里没有这一行"
        assert row["template"] == "brief", row["template"]

    def test_not_sending_a_template_keeps_the_previous_behaviour(self, recorded):
        """反向对照：不传模板 = 用默认模板 = 与加这个字段之前一致。"""
        s, uid = recorded
        _summarize(uid)
        assert s.template == prompt_template.default_template_id()
        row = database.get_video_by_url(URL)
        assert row["template"] == prompt_template.default_template_id()


class TestPromptFollowsTheTemplate:
    """发给模型的那份提示词，必须真的是所选模板的那份。"""

    def test_each_template_renders_its_own_prompt(self):
        for template_id, marker in TEMPLATE_MARKERS.items():
            prompt = summarizer.VideoSummarizer._build_full_prompt(
                "字幕正文", "zh", template_id
            )
            assert marker in prompt, f"{template_id} 的提示词里没有它自己的标记"
            for other_id, other_marker in TEMPLATE_MARKERS.items():
                if other_id == template_id:
                    continue
                assert other_marker not in prompt, (
                    f"{template_id} 的提示词里混进了 {other_id} 的标记"
                )

    def test_omitting_the_template_equals_asking_for_the_default(self):
        """反向对照：不传模板与传默认模板渲染出**逐字相同**的提示词。"""
        without = summarizer.VideoSummarizer._build_full_prompt("字幕正文", "zh")
        with_default = summarizer.VideoSummarizer._build_full_prompt(
            "字幕正文", "zh", prompt_template.default_template_id()
        )
        assert without == with_default

    def test_a_chosen_template_still_gets_its_placeholders_filled(self):
        prompt = summarizer.VideoSummarizer._build_full_prompt("字幕正文", "zh", "brief")
        assert "字幕正文" in prompt
        assert summarizer.MINDMAP_TAGS_SENTINEL in prompt
        assert tags.vocabulary_prompt_text()[:20] in prompt
        assert "$subtitle" not in prompt, "占位符没被填，模型会收到字面量"
        assert "$vocabulary" not in prompt
        assert "{{" not in prompt and "}}" not in prompt, "JSON 花括号被转义了"


class TestEveryTemplateIsSelfContained:
    """每份模板自带完整的一套（不引用别的模板）。"""

    def test_each_template_file_carries_its_own_sections(self):
        for template_id in TEMPLATE_MARKERS:
            path = prompt_template.prompt_path(prompt_template.template_file(template_id))
            text = path.read_text(encoding="utf-8")
            for section in ("【第一部分", "【第二部分", "【第三部分", "【输出格式"):
                assert section in text, f"{path.name} 缺 {section} 那一段"
            for placeholder in ("$lang_hint", "$vocabulary", "$sentinel", "$subtitle"):
                assert placeholder in text, f"{path.name} 缺占位符 {placeholder}"

    def test_each_template_ships_a_complete_example(self):
        """每份模板都要有**自己**的完整示例（few-shot 的形状权重最高）。"""
        for template_id in TEMPLATE_MARKERS:
            path = prompt_template.prompt_path(prompt_template.template_file(template_id))
            text = path.read_text(encoding="utf-8")
            assert "完整示例" in text, f"{path.name} 没有示例段"
            samples = _mindmap_samples(text)
            assert samples, f"{path.name} 的示例里没有导图 JSON"
            example = text.split("完整示例", 1)[1]
            assert re.search(r"^# \S", example, re.M), f"{path.name} 的示例没有一级标题"
            assert re.search(r"^## \S", example, re.M), f"{path.name} 的示例没有二级标题"

    def test_every_template_example_obeys_the_mindmap_structure_rules(self):
        """导图段是产品规则（工单 #106 / ADR 0027），四份模板都要守。

        判据取自 ADR 0027 定的结构上限：根标题唯一、最深到三级、
        至少两个二级分支、节点不超过 30 个。
        """
        for template_id in TEMPLATE_MARKERS:
            path = prompt_template.prompt_path(prompt_template.template_file(template_id))
            samples = _mindmap_samples(path.read_text(encoding="utf-8"))
            for sample in samples:
                headings = [
                    line for line in sample.splitlines()
                    if re.match(r"^#{1,6}\s+\S", line.strip())
                ]
                levels = [len(h) - len(h.lstrip("#")) for h in headings]
                roots = [h for h in headings if h.strip().startswith("# ")]
                branches = [h for h in headings if h.strip().startswith("## ")]
                assert len(roots) == 1, f"{template_id} 的示例有 {len(roots)} 个根标题"
                assert max(levels) >= 3, f"{template_id} 的示例最深只到 {max(levels)} 级"
                assert len(branches) >= 2, f"{template_id} 的示例只有 {len(branches)} 个分支"
                nodes = len(headings) + len(
                    [x for x in sample.splitlines() if re.match(r"^[-*+]\s+\S", x.strip())]
                )
                assert nodes <= 30, f"{template_id} 的示例有 {nodes} 个节点"


class TestLegacyDatabaseUpgrade:
    """老库升级：列要补上，老行要被回填（工单 #105 / ADR 0026）。"""

    def _seed_legacy(self, legacy_db):
        legacy_db.init_db()
        with legacy_db.get_db() as c:
            c.execute("DROP TABLE videos")
            c.execute("DROP TABLE IF EXISTS videos_fts")
            c.execute(_LEGACY_VIDEOS_DDL)
            c.execute(
                "INSERT INTO videos (video_url, status, summary_md) VALUES (?, ?, ?)",
                (URL, "ready", "老库里的总结"),
            )

    def test_the_column_is_added_to_an_old_database(self, legacy_db):
        self._seed_legacy(legacy_db)
        with legacy_db.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(videos)")}
        assert "template" not in cols, "前提不成立：老库不该有 template 列"

        legacy_db.init_db()

        with legacy_db.get_db() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(videos)")}
        assert "template" in cols, "升级后没有 template 列，代码一 SELECT 就报错"

    def test_old_rows_are_filled_with_the_default_template(self, legacy_db):
        """老行是在「还没有模板这回事」时解析的，用的就是默认那份。"""
        self._seed_legacy(legacy_db)

        legacy_db.init_db()

        row = legacy_db.get_video_by_url(URL)
        assert row is not None, "升级把存量行弄没了"
        assert row["template"] == prompt_template.default_template_id(), row["template"]
        assert row["summary_md"] == "老库里的总结", "升级把内容抹了"


def _mindmap_samples(text: str) -> list[str]:
    """提示词里所有 ``{"mindmap": ...}`` 样例的导图正文。

    逐行找：JSON 样例在提示词里是**独占一行**的，解析不出整行 JSON
    的行不算（那多半是散文里提到 JSON 的句子）。
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('{"mindmap"'):
            continue
        start = stripped.index("{")
        end = stripped.rindex("}") + 1
        try:
            data = json.loads(stripped[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(data.get("mindmap"), str):
            out.append(data["mindmap"])
    return out
