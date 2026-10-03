"""把「触库测试必须取 db 夹具」这条门禁接进 pytest 全量。

为什么要有这一条：conftest 声明了「绝不碰 backend/data/app.db」，
但那是注释不是强制。漏取夹具的测试会静默连上开发机的真实库。
上一轮复审实测到 4 条测试打开了真实 app.db 并产生 -wal/-shm 边文件。

本文件被 pytest 收集时会把门禁变成一条普通测试；也可单独运行
`python tests/check_db_fixture.py`。

注意：门禁本身也必须有测试。否则把 scan() 改成恒返回空，全量照样全绿——
门禁自己就成了摆设。
"""
import textwrap

import check_db_fixture


def test_every_db_touching_test_uses_the_db_fixture():
    offenders = check_db_fixture.scan()
    assert offenders == [], (
        "以下测试会触库但未取 db 夹具，会连到真实 backend/data/app.db：\n"
        + "\n".join(f"  {f}::{n}  调用={c}" for f, n, c in offenders)
        + "\n\n修法：给测试加上 db 夹具参数，例如 def test_x(self, db):"
    )


class TestGuardItselfWorks:
    """门禁的正例：给它违规代码，它必须报出来。

    没有这一组的话，把 scan() 改成永远返回空列表就能让门禁失效，
    而全量测试照样全绿——「门禁失效」与「门禁通过」将无法区分。
    """

    def _write_and_scan(self, tmp_path, source: str):
        path = tmp_path / "test_sample.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return check_db_fixture.find_offenders(path)

    def test_guard_flags_test_that_touches_db_without_fixture(self, tmp_path):
        offenders = self._write_and_scan(tmp_path, """
            import database

            def test_bad():
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """)
        assert [n for n, _ in offenders] == ["test_bad"], (
            f"门禁没能认出漏取夹具的测试：{offenders}"
        )

    def test_guard_passes_test_with_fixture(self, tmp_path):
        offenders = self._write_and_scan(tmp_path, """
            import database

            def test_good(db):
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """)
        assert offenders == [], f"门禁误报了合规测试：{offenders}"

    def test_guard_ignores_pure_stub_tests(self, tmp_path):
        """纯桩计数测试不碰数据库，不该被要求取 db 夹具。"""
        offenders = self._write_and_scan(tmp_path, """
            class Stub:
                def __init__(self):
                    self.calls = 0

            def test_stub_only():
                s = Stub()
                s.calls += 1
                assert s.calls == 1
        """)
        assert offenders == [], f"门禁误报了不触库的测试：{offenders}"

    def test_guard_recognizes_indirect_fixture(self, tmp_path):
        """经 client_app 间接拿到 db 夹具的也算合规。"""
        offenders = self._write_and_scan(tmp_path, """
            import database

            def test_via_client_app(client_app):
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """)
        assert offenders == [], f"门禁误报了经 client_app 取夹具的测试：{offenders}"

    def test_guard_accepts_legacy_db_fixture(self, tmp_path):
        """legacy_db 是 db 的「只隔离、不建表」版本，升级测试必须能用它。

        db 夹具会先 init_db() 造一张全新表，老库升级那条路径在它下面走不到；
        而 legacy_db 同样换了 DB_PATH、同样清连接，隔离强度没有更松。
        """
        offenders = self._write_and_scan(tmp_path, """
            import database

            def test_upgrade(legacy_db):
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """)
        assert offenders == [], f"门禁误报了取 legacy_db 的升级测试：{offenders}"

    def test_guard_exemption_is_not_a_blanket_pass(self, tmp_path):
        """豁免只认 legacy_db 这一个名字，取别的夹具照样要报出来。

        少了这一条，把 DB_FIXTURE_ARGS 改成随便什么名字都能让门禁失效，
        而全量测试照样全绿——「门禁失效」与「门禁通过」将无法区分。
        """
        offenders = self._write_and_scan(tmp_path, """
            import database

            def test_wrong_fixture(tmp_path):
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """)
        assert [n for n, _ in offenders] == ["test_wrong_fixture"], (
            f"门禁对非 db 类夹具放行了：{offenders}"
        )

    def test_scan_is_not_hardcoded_empty(self, tmp_path, monkeypatch):
        """scan() 必须真的扫目录，不能被改成恒返回空。

        find_offenders 的正例只证明单文件解析对；scan() 是全量入口，
        这里直接给它一个违规文件，看它是否报得出来。
        """
        bad = tmp_path / "test_injected_bad.py"
        bad.write_text(textwrap.dedent("""
            import database

            def test_injected():
                with database.get_db() as c:
                    c.execute("SELECT 1")
        """), encoding="utf-8")

        monkeypatch.setattr(
            check_db_fixture, "TESTS_DIR", tmp_path,
        )
        offenders = check_db_fixture.scan()

        assert any("test_injected" in name for _, name, _ in offenders), (
            f"scan() 没扫出注入的违规文件：{offenders}"
        )
