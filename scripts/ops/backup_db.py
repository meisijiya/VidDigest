"""在线备份：出一份一致的 SQLite 快照，校验完整性，按份数保留（工单 #63）。

**为什么不能直接拷文件**：本仓库的库是 WAL 模式，服务在跑的时候
「当前状态」不全在 app.db 里——多出来的部分在 app.db-wal 里。直接
`cp app.db` 拿到的是缺事务的快照，恢复它会丢数据，而且丢得很安静。

**为什么用 SQLite 自带的在线备份**：`sqlite3.Connection.backup` 会在
不阻塞写入的前提下，把主库 + WAL 合成一份一致副本。这正是我们要的
「热备份」。

**为什么备份完还要校验**：一份打不开、或者 integrity_check 不过的
备份，比没有备份更危险——它会让人以为可以恢复。所以校验不过就当失败，
退出码非零。

**为什么文件名精确到微秒**：一秒钟里跑两次备份是常事（定时任务与手动触发
撞在一起）。秒级时间戳会让后一份**盖掉**前一份——看着像备份了两份，实际
只剩一份，而且不报错。

用法：
    python scripts/ops/backup_db.py
    python scripts/ops/backup_db.py --keep 14
    python scripts/ops/backup_db.py --db backend/data/app.db --out-dir backend/backups

退出码：0 = 备份成功且完整性校验通过；非 0 = 失败（不覆盖已有备份）。
"""
import argparse
import datetime
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(REPO, "backend", "data", "app.db")
DEFAULT_OUT_DIR = os.path.join(REPO, "backend", "backups")
DEFAULT_KEEP = 7
PREFIX = "app-"
SUFFIX = ".db"

# Windows 控制台默认不是 UTF-8，中文路径会变成乱码——输出要给人看
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def backup_once(src_path, out_path):
    """出一份一致快照。源库不存在或损坏时抛异常，不写半个文件。"""
    src = sqlite3.connect(src_path, timeout=30)
    try:
        dst = sqlite3.connect(out_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def integrity_ok(path):
    """PRAGMA integrity_check 通过才算一份可用的备份。"""
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and str(row[0]).strip().lower() == "ok"
    finally:
        conn.close()


def new_backup_path(out_dir):
    """给这次备份挑一个**不会撞名**的文件名。

    为什么：时间戳原本精确到秒，而「一秒钟里跑两次备份」是常事——定时任务
    与手动触发撞在一起、备份脚本连着跑两遍。两个同名文件里**后一个把前一个
    盖掉了**：看着像备份了两份，实际只剩一份，而且没有任何报错。

    做法：先到微秒；万一还撞（极小概率，或测试把时间冻住了），就加序号
    -2、-3……直到不撞为止。微秒让「按文件名排序 = 按时间排序」仍然成立，
    `prune()` 的保留策略照旧按名字排。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = os.path.join(out_dir, "%s%s%s" % (PREFIX, stamp, SUFFIX))
    seq = 2
    while os.path.exists(path):
        path = os.path.join(out_dir, "%s%s-%d%s" % (PREFIX, stamp, seq, SUFFIX))
        seq += 1
    return path


def prune(out_dir, keep):
    """只保留最新的 keep 份，返回被删掉的文件名。"""
    if keep <= 0:
        return []
    names = sorted(
        n for n in os.listdir(out_dir)
        if n.startswith(PREFIX) and n.endswith(SUFFIX)
    )
    removed = []
    for name in names[:-keep]:
        os.remove(os.path.join(out_dir, name))
        removed.append(name)
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite 在线备份 + 完整性校验 + 保留策略")
    parser.add_argument("--db", default=DEFAULT_DB, help="源库路径")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="备份输出目录")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留份数（最旧的多余删掉）")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        sys.stderr.write("ERROR: 源库不存在：%s\n" % args.db)
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = new_backup_path(args.out_dir)

    try:
        backup_once(args.db, out_path)
    except sqlite3.Error as exc:
        if os.path.exists(out_path):
            os.remove(out_path)
        sys.stderr.write("ERROR: 备份失败：%s\n" % exc)
        return 1

    if not integrity_ok(out_path):
        size = os.path.getsize(out_path)
        sys.stderr.write(
            "ERROR: 备份完整性校验未通过，已保留现场供排查：%s（%d 字节）\n"
            % (out_path, size)
        )
        return 1

    size = os.path.getsize(out_path)
    removed = prune(args.out_dir, args.keep)
    print("备份完成：%s（%d 字节）" % (out_path, size))
    print("完整性校验：ok")
    if removed:
        print("按保留策略清理旧档 %d 份：%s" % (len(removed), ", ".join(removed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
