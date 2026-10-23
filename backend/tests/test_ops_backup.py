"""`scripts/ops/backup_db.py` 的备份落盘行为（工单 #68）。

## 为什么这个脚本此前没有用例

`init.sh` 的 pytest 只收集 `backend/tests/`，而两个运维脚本
（`scripts/ops/backup_db.py`、`scripts/ops/inspect_runtime.py`）一直靠
**人肉跑**验证：跑一次、看一眼输出、相信它。

## 这条路径上真出过的事

备份文件名原本精确到**秒**（`%Y%m%d-%H%M%S`）。一秒钟里跑两次备份是常事——
定时任务与手动触发撞在一起、备份脚本连着跑两遍。两次落到同一个文件名，
**后一次把前一次盖掉了**：看着像备份了两份，实际只剩一份，而且**没有任何
报错**，退出码还是 0。

「备份了 2 份」与「磁盘上有 2 个文件」在这种实现下是两件事：后者成立，
前者不成立。

## 判据读的是外部可观察的东西

磁盘上**有几个文件**、每个文件**能不能打开**、**里面装的是哪一刻的库**。
不测私有函数，不断言实现细节。

## 为什么把时间冻住

「一秒钟里跑两次」这件事在真实世界里是概率事件，在测试里必须**是**事实。
所以把脚本内部取时间的入口换成一个固定时刻的假实现——那一刻两次备份
必然落在同一个秒里，于是「会不会互相覆盖」就是确定的答案，而不是看运气。

反向对照：同一个文件里另有一条用例把时间**放开**，两次备份自然落在不同时刻，
那种情况下本来就该有两份——它保证「有两份」不是因为固定时刻这条特例。
"""
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "ops" / "backup_db.py"


def _load_backup_module():
    """按路径加载脚本（它不在包里，也不是可导入的模块）。"""
    spec = importlib.util.spec_from_file_location("ops_backup_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup_db = _load_backup_module()


class _FrozenDatetime:
    """固定在 2026-01-02 03:04:05 的假时间源。"""

    FIXED = None  # 由 _frozen_clock 填

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED


class _FrozenDatetimeModule:
    """顶掉脚本里的 `datetime.datetime.now()`。"""

    datetime = _FrozenDatetime


def _make_source_db(path, rows, reset=False):
    conn = sqlite3.connect(path)
    try:
        if reset:
            conn.execute("DROP TABLE IF EXISTS notes")
        conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)")
        for body in rows:
            conn.execute("INSERT INTO notes (body) VALUES (?)", (body,))
        conn.commit()
    finally:
        conn.close()


def _backup_files(out_dir):
    return sorted(p for p in Path(out_dir).glob("app-*.db"))


def _rows_in(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        conn.close()


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path, monkeypatch):
    """核心判据：同一秒里跑两次，两份文件都得在，且各装各的内容。"""
    import datetime

    source = tmp_path / "app.db"
    out_dir = tmp_path / "backups"
    _make_source_db(source, ["first"])

    frozen = datetime.datetime(2026, 1, 2, 3, 4, 5)
    monkeypatch.setattr(backup_db, "datetime", _FrozenDatetimeModule)
    _FrozenDatetime.FIXED = frozen

    assert backup_db.main(["--db", str(source), "--out-dir", str(out_dir)]) == 0

    # 源库变了：第二次备份必须装的是**这一刻**的内容。
    _make_source_db(source, ["first", "second"], reset=True)
    assert backup_db.main(["--db", str(source), "--out-dir", str(out_dir)]) == 0

    files = _backup_files(out_dir)
    assert len(files) == 2, (
        f"同一秒里备份两次，磁盘上只剩 {len(files)} 个文件 —— 后一次盖掉了前一次"
    )
    contents = sorted(_rows_in(p) for p in files)
    assert contents == [["first"], ["first", "second"]], (
        f"两份备份的内容是 {contents}，说明有一份被盖成了另一份"
    )


def test_backups_in_different_moments_also_stay_separate(tmp_path):
    """反向对照：不冻时间时，两次备份同样不该互相覆盖。"""
    source = tmp_path / "app.db"
    out_dir = tmp_path / "backups"
    _make_source_db(source, ["only"])

    assert backup_db.main(["--db", str(source), "--out-dir", str(out_dir)]) == 0
    assert backup_db.main(["--db", str(source), "--out-dir", str(out_dir)]) == 0

    assert len(_backup_files(out_dir)) == 2


def test_the_backup_passes_its_own_integrity_check(tmp_path):
    """备份出来的东西必须自己打得开——它是拿去恢复的。"""
    source = tmp_path / "app.db"
    out_dir = tmp_path / "backups"
    _make_source_db(source, ["x", "y"])

    assert backup_db.main(["--db", str(source), "--out-dir", str(out_dir)]) == 0
    files = _backup_files(out_dir)
    assert len(files) == 1
    assert backup_db.integrity_ok(str(files[0]))
    assert _rows_in(files[0]) == ["x", "y"]


def test_prune_keeps_the_newest_files(tmp_path):
    """保留策略：留最新的 keep 份。"""
    out_dir = tmp_path / "backups"
    out_dir.mkdir()
    for i in range(5):
        (out_dir / ("app-2026010%d-000000-00000%d.db" % (i, i))).write_bytes(b"x")

    removed = backup_db.prune(str(out_dir), 2)
    left = sorted(p.name for p in _backup_files(out_dir))
    assert left == ["app-20260103-000000-000003.db", "app-20260104-000000-000004.db"]
    assert len(removed) == 3


def test_a_missing_source_is_refused(tmp_path):
    """源库不存在时退出码非零，且不留下半成品。"""
    out_dir = tmp_path / "backups"
    rc = backup_db.main(["--db", str(tmp_path / "nope.db"), "--out-dir", str(out_dir)])
    assert rc == 2
    assert not _backup_files(out_dir)
