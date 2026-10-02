import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")

# 线程本地连接缓存（uvicorn 线程池复用线程，连接随之复用）
_thread_local = threading.local()

# 全部已建立连接（含线程池工作线程那份），供测试夹具彻底清理。
# threading.local() 的属性只有持有它的线程能删，主测试线程拿不到工作线程那份，
# 因此必须在这里留一份清单。
#
# 强引用是必须的（Connection 不支持弱引用，实测 weakref.WeakSet 直接抛
# TypeError: cannot create weak reference to 'sqlite3.Connection' object），
# 代价是「线程死了但清单还留着它的连接」会造成 fd 滞留。因此每次登记前
# 先剔除已死线程的条目——threading.enumerate() 只列活线程。
_open_conns = []
#: 每份连接由哪个线程创建（与 _open_conns 同序）。
#: 测试要证明「工作线程那份确实被登记了」，光数数量不够——
#: 同一份连接被登记两次也能让计数达标。
_open_conns_threads = []
_open_conns_lock = threading.Lock()

# 连接代际号。清理连接时递增，所有线程下次取连接时发现代际变了就重建。
#
# 为什么不能靠「试一次 execute 看连接是否已关闭」：sqlite3 禁止跨线程使用
# 连接，主线程对一份**仍打开**的连接执行 execute 会抛
#   ProgrammingError: SQLite objects created in a thread can only be used in
#   that same thread
# 而对一份**已关闭**的连接抛的是
#   ProgrammingError: Cannot operate on a closed database
# 两者异常类型相同，只有消息文本能区分——也就是说，在主线程用
# `pytest.raises(sqlite3.ProgrammingError)` 断言「连接已关闭」恒真，
# 测不出任何东西（这正是本工单早期版本里一条假通过的断言）。
# 代际号不依赖连接对象的内部状态，跨线程可靠。
_conn_generation = 0


def get_connection_generation() -> int:
    return _conn_generation

MAX_PARSE_HISTORY_PER_USER = 30

# 两个额度上限都从环境变量读取，改配置不必发版。
# 各自独立取值：解析产出内容、追问消耗对话，用量节奏本就不同。
def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        # 配错时退回默认值，而不是让整个应用起不来——
        # 一个环境变量写错不该让整个站点不可用。
        return default


DAILY_PARSE_LIMIT = _env_int("VIDDIGEST_DAILY_PARSE_LIMIT", 3)
DAILY_CHAT_LIMIT = _env_int("VIDDIGEST_DAILY_CHAT_LIMIT", 10)

#: 额度种类 → (计数字段, 日期字段, 上限常量名)
#: 两个计数器在同一张 users 表上，但各占自己的列与日期字段。
_QUOTA_KINDS = {
    "parse": ("daily_parse_count", "last_parse_date", "DAILY_PARSE_LIMIT"),
    "chat": ("daily_chat_count", "last_chat_date", "DAILY_CHAT_LIMIT"),
}


def get_db_path():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return DB_PATH


def _prune_dead_threads_locked() -> None:
    """剔除已死线程的连接条目（调用方须已持锁）。

    线程死掉后，它的 threading.local 一并销毁，连接本可被 GC 回收；
    但 _open_conns 强引用着它，不剔除就会一直占着 OS 文件句柄。
    实测：200 个短命线程，不剔除时注册表滞留 201 条、句柄净增 401。

    sqlite3.Connection 不支持弱引用（weakref.WeakSet 抛 TypeError），
    只能靠「线程是否还活着」判断，threading.enumerate() 正好只列活线程。

    快路径比的是**身份**不是数量：曾用 `len(alive) == len(登记数)` 直接跳过，
    但死线程数与新活线程数相等时那两条判据会同时成立，死条目被漏掉
    （实测残留 2 条，每条 WAL 连接占 2~3 个句柄）。
    """
    alive = {t.ident for t in threading.enumerate()}
    if _open_conns_threads and alive.issuperset(_open_conns_threads):
        return  # 每条登记都属于活线程，无需逐一比对

    keep = [i for i, owner in enumerate(_open_conns_threads) if owner in alive]
    if len(keep) == len(_open_conns_threads):
        return

    _open_conns[:] = [_open_conns[i] for i in keep]
    _open_conns_threads[:] = [_open_conns_threads[i] for i in keep]


def open_connections():
    """当前进程内所有未关闭的数据库连接（跨线程）。

    仅供测试夹具使用：它需要清掉线程池工作线程持有的连接，
    而那些连接存在各自的 threading.local() 里，外部无法直接触达。
    """
    with _open_conns_lock:
        _prune_dead_threads_locked()
        return list(_open_conns)


def open_connection_threads():
    """与 open_connections() 同序的「创建线程 ident」列表。

    单独给出是因为「工作线程那份被登记了」这件事，光数数量证明不了：
    同一份连接被登记两次也能让计数达标。

    这里也调剔除——否则「与 open_connections() 同序」不总成立：
    先读 threads 再读 conns 时，中间若有线程死亡，两者长度就会不等。
    """
    with _open_conns_lock:
        _prune_dead_threads_locked()
        return list(_open_conns_threads)


def forget_all_connections():
    """关闭并清空全部已建立连接，同时递增代际号。

    sqlite3 的连接归创建它的线程所有，**主线程 close 不了工作线程那份**：
    会抛 ProgrammingError: objects created in a thread can only be used in
    that same thread。threading 又没有「按线程 ident 投递任务」的 API，
    因此工作线程的连接只能等它自己在下次 get_db() 时因代际号变化而丢弃，
    届时引用计数归零、连接被 GC 回收（sqlite3.Connection.__del__ 会 close）。

    所以这里对工作线程的连接不做 close，只清登记；对主线程自己的那份
    直接 close——那条是真能关掉的。跨线程 close 抛错被静默吞掉，
    等于一个都没关，假装关了才是更糟的错误。

    递增代际号解决的是「别再用旧连接」，与「立刻关掉旧连接」是两件事：
    前者是正确性保证（由测试守住），后者只是资源回收的尽力而为。
    """
    global _conn_generation
    current = threading.get_ident()
    with _open_conns_lock:
        _conn_generation += 1
        _prune_dead_threads_locked()
        owned_by_current = [
            conn for conn, owner in zip(_open_conns, _open_conns_threads)
            if owner == current
        ]
        _open_conns.clear()
        _open_conns_threads.clear()

    for conn in owned_by_current:
        try:
            conn.close()
        except sqlite3.Error:
            pass


@contextmanager
def get_db():
    """线程本地连接复用：WAL/外键等 PRAGMA 仅在新连接时设置一次，
    避免每次操作重建连接的开销（实测单次查询 155ms → 亚毫秒级）。"""
    with _open_conns_lock:
        generation = _conn_generation

    conn = getattr(_thread_local, "conn", None)
    if conn is not None and getattr(_thread_local, "generation", None) != generation:
        # 连接属于上一代（已被清理或换过库），本线程还拿着它。
        # 必须重建，否则工作线程会写进上一个测试的库。
        conn = None
        _thread_local.conn = None

    if conn is None:
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _thread_local.conn = conn
        _thread_local.generation = generation
        with _open_conns_lock:
            _prune_dead_threads_locked()
            _open_conns.append(conn)
            _open_conns_threads.append(threading.get_ident())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """初始化数据库表结构"""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_vip INTEGER DEFAULT 0,
                vip_expire_at TEXT,
                daily_summary_count INTEGER DEFAULT 0,
                last_summary_date TEXT,
                daily_parse_count INTEGER DEFAULT 0,
                last_parse_date TEXT,
                daily_chat_count INTEGER DEFAULT 0,
                last_chat_date TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_no TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'cny',
                status TEXT DEFAULT 'pending',
                plan_type TEXT DEFAULT 'monthly',
                stripe_session_id TEXT UNIQUE,
                stripe_payment_intent_id TEXT,
                paid_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
            CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);

            CREATE TABLE IF NOT EXISTS parse_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                video_url TEXT NOT NULL,
                video_title TEXT DEFAULT '',
                video_data TEXT DEFAULT '',
                summary_md TEXT DEFAULT '',
                mindmap_md TEXT DEFAULT '',
                subtitle_data TEXT DEFAULT '',
                chat_history TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_history_user ON parse_history(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_history_user_url ON parse_history(user_id, video_url);

            -- 社区视频表（ADR 0001）：全站共享，一个链接只有一行。
            --
            -- 与 parse_history 的分工：那张表是「谁解析过什么」的个人记录，
            -- 每用户滚动保留 MAX_PARSE_HISTORY_PER_USER 条；这张表是社区内容本身，
            -- 任何人都能读，且**刻意不做条数裁剪**——裁剪会直接摧毁社区的价值
            -- （A 解析的视频被裁掉，B 就再也复用不到了）。
            --
            -- video_url 的唯一性靠下面那条全局唯一索引，而不是表内 UNIQUE 约束：
            -- 它是「同一链接全站只解析一次」这条承诺的落地点，必须有自己的名字，
            -- 迁移与排查时能直接指认（parse_history 那条 idx_history_user_url 含
            -- user_id，无法复用）。
            --
            -- status = 'pending' 的行是**占位**：占位者才是首次解析者，由他去调模型。
            -- 已 ready 的行谁都不能改写（complete_video 只更新自己占的那一行）。
            --
            -- parsed_by 不建外键：解析者注销后社区内容必须留下来。
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                summary_md TEXT DEFAULT '',
                mindmap_md TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                subtitle_text TEXT DEFAULT '',
                parsed_by INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_url ON videos(video_url);

            -- 追问会话（工单 #8）：明细表。一次「会话」= (user_id, video_url) 的聚合。
            --
            -- **刻意不做条数裁剪**：parse_history 的 30 条滚动规则是它自己那张表的
            -- 规矩，与本表无关。追问记录被裁掉是静默的数据丢失——用户问过的东西
            -- 不该因为他最近解析得有点多就消失。工单点名的缺陷正是这个。
            --
            -- role 拆成两行（一问一行、一答一行），而不是 {question, answer} 一行：
            -- 「最近 3 轮」天然是消息序列，按 id 取最近 2 倍条数即可，不用反解配对；
            -- 而两列结构会让断流时那半条「有问无答」的记录**看起来完整**，
            -- 于是被当成有效历史塞进模型上下文。
            --
            -- 不建 user_id 外键：与 videos.parsed_by 同一理由——
            -- 用户注销后会话记录应随该用户一起消失，而不是变成孤儿行。
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                video_url TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_chat_user_url ON chat_messages(user_id, video_url, id);
        """)

        _migrate_quota_columns(conn)
        _migrate_video_card_columns(conn)
        _create_video_search_index(conn)


def _migrate_quota_columns(conn) -> None:
    """给已存在的 users 表补上额度拆分需要的新列（expand 阶段）。

    为什么必须单独一步：`CREATE TABLE IF NOT EXISTS` 对已存在的表是空操作，
    老库里不会有新列，代码一读就报 no such column。

    逐列判断再 ALTER：SQLite 没有 `ADD COLUMN IF NOT EXISTS`，
    重复执行会抛 duplicate column name，所以先查列是否存在。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    for column, ddl in (
        ("daily_parse_count", "ALTER TABLE users ADD COLUMN daily_parse_count INTEGER DEFAULT 0"),
        ("daily_chat_count", "ALTER TABLE users ADD COLUMN daily_chat_count INTEGER DEFAULT 0"),
        ("last_parse_date", "ALTER TABLE users ADD COLUMN last_parse_date TEXT"),
        ("last_chat_date", "ALTER TABLE users ADD COLUMN last_chat_date TEXT"),
    ):
        if column not in existing:
            conn.execute(ddl)


# ── 社区浏览与搜索（工单 #7）─────────────────────────────────
#
# 可见性是这个工单的全部难点：社区列表对**任何人**开放，而字幕、总结、
# 思维导图只对已登录的人开放。两条规则一旦写反就是真实的隐私事故，
# 所以下面的实现有一条硬约定——对外响应**按字段白名单投影**，不靠逐个剔除。


def _migrate_video_card_columns(conn) -> None:
    """给 videos 补上社区卡片要用的两列（expand 阶段，与额度列同一套路）。

    为什么必须单独一步：`CREATE TABLE IF NOT EXISTS videos` 对已存在的表
    是空操作，#6 建的老库里不会凭空多出列，而列表要显示的标题与封面恰恰
    是 #6 建表时没有的。不补列，代码一 SELECT 就报 no such column。

    只**新增**两列可空带默认值的列。videos 已有的每一列、唯一索引、
    pending/ready 状态机都不动：那是 #6 定下的承诺，不该被后一张工单
    顺带改掉——两张表职责分离的边界仍然是 #6 划的那条。

    标题与封面由谁写入：解析时服务端拿不到它们（字幕流里没有平台标题，
    也没有缩略图地址），因此由前端解析成功后回填，见 publish_video_card。
    在此之前这两列为空串——社区列表会显示占位文本，但**不会**显示错误的值。
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)")}
    for column, ddl in (
        ("video_title", "ALTER TABLE videos ADD COLUMN video_title TEXT DEFAULT ''"),
        ("cover_url", "ALTER TABLE videos ADD COLUMN cover_url TEXT DEFAULT ''"),
    ):
        if column not in existing:
            conn.execute(ddl)


#: 全文检索索引表名。
#:
#: 它是 videos 的**派生索引**，不是第二份数据：content='videos' 让索引的行
#: 直接指向内容表的那一行，存两份文本就意味着两份可能不一致的数据——
#: 那正是工单 #7 要消灭的「同一链接两份总结」的同源问题。
_VIDEO_SEARCH_INDEX = "videos_fts"

#: FTS5 分词器。
#:
#: 中文只能用 trigram：默认的 unicode61 按空白与标点切词，一整句没有空格
#: 的中文会被当成**一个**词，于是搜「异步」永远匹配不上「深入理解异步编程」
#: ——实测召回 0。trigram 按 3 字符滑窗切，中文子串因此可召回。
_VIDEO_SEARCH_TOKENIZER = "trigram"

#: trigram 的最小可匹配长度：不足 3 个字符的词在索引里没有对应 trigram，
#: 查了必然是空（实测 2 字中文词召回 0，3 字起正常）。
#: 按标签精确筛选不受此限——它走 json_each，不经 FTS。
MIN_FTS_TERM_CHARS = 3

_VIDEO_SEARCH_TRIGGER_SETUP = f"""
    CREATE TRIGGER IF NOT EXISTS videos_fts_ai AFTER INSERT ON videos BEGIN
        INSERT INTO {_VIDEO_SEARCH_INDEX}(rowid, video_title, tags, video_url)
        VALUES (new.id, new.video_title, new.tags, new.video_url);
    END;

    CREATE TRIGGER IF NOT EXISTS videos_fts_ad AFTER DELETE ON videos BEGIN
        INSERT INTO {_VIDEO_SEARCH_INDEX}({_VIDEO_SEARCH_INDEX}, rowid,
            video_title, tags, video_url)
        VALUES ('delete', old.id, old.video_title, old.tags, old.video_url);
    END;

    CREATE TRIGGER IF NOT EXISTS videos_fts_au AFTER UPDATE ON videos BEGIN
        INSERT INTO {_VIDEO_SEARCH_INDEX}({_VIDEO_SEARCH_INDEX}, rowid,
            video_title, tags, video_url)
        VALUES ('delete', old.id, old.video_title, old.tags, old.video_url);
        INSERT INTO {_VIDEO_SEARCH_INDEX}(rowid, video_title, tags, video_url)
        VALUES (new.id, new.video_title, new.tags, new.video_url);
    END;
"""


def _create_video_search_index(conn) -> None:
    """建（或补齐）社区全文检索索引：外部内容 FTS5 表 + 三个同步触发器。

    触发器是这张表与 videos 之间唯一的同步机制，写在库里而不是应用代码里：
    任何一条 UPDATE videos 的路径——包括 complete_video——都会自动重建该行的
    索引，不必记得回头调一次「重新索引」。漏掉一次就是一条静默搜不到的视频。

    'rebuild' 幂等：老库里在触发器建立之前就有的行会被全量灌进索引，
    重复执行只是重建，不会重复插入。
    """
    conn.executescript(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_VIDEO_SEARCH_INDEX} USING fts5(
            video_title, tags, video_url,
            content='videos', content_rowid='id',
            tokenize='{_VIDEO_SEARCH_TOKENIZER}'
        );
    """)
    conn.executescript(_VIDEO_SEARCH_TRIGGER_SETUP)
    conn.execute(
        f"INSERT INTO {_VIDEO_SEARCH_INDEX}({_VIDEO_SEARCH_INDEX}) VALUES ('rebuild')"
    )


# ── 可见性：对外投影的字段白名单 ────────────────────────────
#
# 白名单，不是黑名单。差别在于**新增列会不会自动泄漏**：
# 逐个剔除的写法里，往 videos 加一列的人不必改任何东西，那一列就已经
# 出现在未登录访客的响应里了；白名单下它根本不会被构造出来。
#
# 键集合由测试逐个断言（set(...) == {...}），不是「断言某个键不在」。

#: 未登录访客在社区**列表**里能看到的全部键。id / video_url 是标识而非内容：
#: 卡片要能被点开、详情要能被寻址，没有它们列表页无法导航；
#: 它们不承载任何字幕 / 总结 / 思维导图文本。
COMMUNITY_CARD_FIELDS = ("id", "video_url", "cover_url", "video_title", "tags")

#: 已登录用户看**详情**时能拿到的键。列表无论登录与否都只用上面的白名单——
#: 列表是公开入口，把内容塞进去等于让未登录访客多拿一份。
COMMUNITY_DETAIL_FIELDS = COMMUNITY_CARD_FIELDS + (
    "summary_md", "mindmap_md", "subtitle_text", "created_at", "updated_at",
)

COMMUNITY_PAGE_SIZE_DEFAULT = 20
COMMUNITY_PAGE_SIZE_MAX = 100


def _project_video(row, fields: tuple) -> dict:
    """按字段白名单投影一行，tags 由 JSON 文本还原成数组。

    tags 的清洗与 get_video_by_url 同一套：类型不对就当没有，
    不让脏数据变成下游的 TypeError。
    """
    item = {name: row[name] for name in fields}
    try:
        parsed = json.loads(item.get("tags") or "[]")
    except (ValueError, TypeError):
        parsed = []
    item["tags"] = [t for t in parsed if isinstance(t, str)] if isinstance(parsed, list) else []
    return item


#: 页码的绝对上界。
#:
#: 它存在的原因不是「怕有人翻太多页」，而是**防整数溢出**：SQLite 的
#: INTEGER 是 64 位有符号，Python 整数却是任意精度。page 没有上界时
#: `?page=9223372036854775807` 会让 OFFSET 变成一个绑不进去的整数，
#: sqlite3 抛 "Python int too large to convert to SQLite INTEGER"，
#: 路由层没捕获 → 500。而社区列表是**公开入口，不需要登录**。
#:
#: 10^9 页 × 每页上限 100 = 10^11，离 2^63 还剩八个数量级的余量，
#: 真实数据量永远到不了这个页数，所以钳到它只是让「翻过头」表现得
#: 与「翻过头没东西」一致，而不是打成 500。
_MAX_SAFE_PAGE = 1_000_000_000


def _clamp_page(page: int, page_size: int) -> tuple[int, int]:
    """页码与页长收敛到安全范围。**两个方向都要收。**

    - page < 1 归一到 1；page 另有上界 _MAX_SAFE_PAGE（否则 OFFSET 溢出）。
    - page_size < 1 归一到 1，上限封顶：它直接进 LIMIT，一个巨大的
      page_size 会让数据库先扫过整张表再丢掉。
    """
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        page, page_size = 1, COMMUNITY_PAGE_SIZE_DEFAULT
    return (max(1, min(page, _MAX_SAFE_PAGE)),
            max(1, min(page_size, COMMUNITY_PAGE_SIZE_MAX)))


def _paginate(from_clause: str, where: str, params: tuple, page: int,
              page_size: int, order_by: str) -> dict:
    """跑一次 count 与一次取页，返回统一的分页信封。

    from / where / order 都由调用方给全，**不套子查询**：FTS5 的 bm25() 是
    辅助函数，必须与 FTS 表在**同一条 SELECT** 里可见，一旦裹进
    `SELECT * FROM ( ... JOIN videos_fts f ... )` 就会报
    no such column: bm25 —— 索引还在，只是排不了序。
    """
    page, page_size = _clamp_page(page, page_size)
    with get_db() as conn:
        total = conn.execute(
            f"SELECT count(*) FROM {from_clause} WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT v.* FROM {from_clause} WHERE {where} ORDER BY {order_by}"
            " LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


#: 社区内容的可见条件。status='ready' 是硬条件：pending 行是**占位**，
#: 里面没有总结也没有字幕，把它当内容展示就是给用户看一个空壳。
#: （brief 与票面都点名过这条。）
_COMMUNITY_VISIBLE = "v.status = 'ready'"


def _tag_clause(tag: str, alias: str = "v") -> tuple[str, tuple]:
    """按标签精确筛选。

    刻意走 json_each 而不是 FTS：trigram 匹配不到 2 个字符的词
    （实测「编程」召回 0），而标签里大量是 2 字词。标签是**枚举值**，
    精确匹配既更快也更准。
    """
    if not tag:
        return "", ()
    return (
        f" AND EXISTS (SELECT 1 FROM json_each({alias}.tags) WHERE value = ?)",
        (tag,),
    )


def list_community_videos(page: int = 1, page_size: int = COMMUNITY_PAGE_SIZE_DEFAULT,
                         tag: str = "") -> dict:
    """社区列表（对未登录访客公开）。返回白名单投影后的行。

    翻页与按标签筛选都在这里，且**不要求登录**——它们是列表本身的用法，
    不是特权。
    """
    clause, params = _tag_clause(tag)
    where = f"{_COMMUNITY_VISIBLE}{clause}"
    order = "v.created_at DESC, v.id DESC"
    result = _paginate("videos v", where, params, page, page_size, order)
    result["items"] = [_project_video(r, COMMUNITY_CARD_FIELDS) for r in result["items"]]
    return result


def get_community_video(video_id: int) -> dict | None:
    """社区视频详情（调用方必须已鉴权）。仅 ready 行。"""
    with get_db() as conn:
        row = conn.execute(
            f"SELECT v.* FROM videos v WHERE v.id = ? AND {_COMMUNITY_VISIBLE}",
            (video_id,),
        ).fetchone()
    return _project_video(row, COMMUNITY_DETAIL_FIELDS) if row else None


def get_community_video_by_url(video_url: str) -> dict | None:
    """按 URL 取社区**卡片**（列表级白名单投影）。没有或尚未 ready 则 None。

    刻意只给卡片、不给详情：这条查询的前端用途是「社区里有没有这一份」
    （要不要重新解析），不是「把内容取回来渲染」。要内容只有详情与
    /api/summarize 两条路，都要求登录。
    """
    with get_db() as conn:
        row = conn.execute(
            f"SELECT v.* FROM videos v WHERE v.video_url = ? AND {_COMMUNITY_VISIBLE}",
            (video_url,),
        ).fetchone()
    return _project_video(row, COMMUNITY_CARD_FIELDS) if row else None


def publish_video_card(video_url: str, video_title: str = "", cover_url: str = "") -> int:
    """回填社区卡片的标题与封面，返回更新的行数。

    三条约束，各有代价，都不是随手加的：

    1. 必须是 ready。占位（pending）行还没有社区内容，此刻写标题等于
       给一个迟早可能被 release_video 删掉的行做卡片，列表会闪出一条
       点进去什么都没有的记录。
    2. 只写这两列。标题封面是**平台元数据**，与谁解析、何时解析无关；
       顺带把 tags 或 summary 一起改掉就是绕过 #6「ready 行谁都不能改写」
       的承诺，从后门开了一条写路径。
    3. **只填空，不覆盖**。标题与封面是用户判断内容和搜索的入口：任何登录
       用户都能改写它，就等于给了一条「把别人的视频改成别的样子」的路子，
       还会静默改动 FTS 索引——与「社区内容不会被别人改写」直接冲突。
       所以已填的字段谁都动不了，先到先得。
       代价：第一个填的人如果填错了，这个字段就错下去了；修它需要人工改库。

    返回值是**诚实的**：SQLite 的 changes() 统计的是「被 UPDATE 语句触及的行」，
    `SET col='same'` 也会算 1。所以 WHERE 里除了「至少一列为空」，还要求
    「传进来的那个值确实非空」——否则第二个只想补封面的调用者会拿到
    updated=1，而实际上一个字段都没写进去。
    """
    if not video_title and not cover_url:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE videos
                  SET video_title = CASE WHEN COALESCE(video_title, '') = '' THEN ? ELSE video_title END,
                      cover_url    = CASE WHEN COALESCE(cover_url, '')    = '' THEN ? ELSE cover_url    END,
                      updated_at   = ?
                WHERE video_url = ? AND status = 'ready'
                  AND ((COALESCE(video_title, '') = '' AND ? <> '')
                    OR (COALESCE(cover_url, '') = '' AND ? <> ''))""",
            (video_title, cover_url,
             datetime.now(timezone.utc).isoformat(), video_url,
             video_title, cover_url),
        )
        return cursor.rowcount


#: 搜索词里要剥掉的字符：全部 C0 控制字符（含 NUL）与 DEL。
#:
#: NUL 是**实测致命**的：它进 FTS5 字符串字面量会让 trigram 分词器判定
#: 字符串未闭合，直接抛 `OperationalError: unterminated string` → 500。
#: 而 `?q=%00` 浏览器与 curl 都发得出去，不是不可达路径。
#:
#: 其余 C0 控制字符实测（LF / TAB）返回 200，但 SQLite 各版本对它们的
#: 处理并无文档化承诺，一并剥掉是防御性做法：搜索词里本来就不该有它们。
_SEARCH_STRIP_CHARS = {c: None for c in range(0x20)}


def _fts_phrase(q: str) -> str:
    """把用户输入包成 FTS5 字符串字面量。

    两件事，缺一不可，顺序也不能换：

    1. **剥控制字符**（NUL 在内）——见 _SEARCH_STRIP_CHARS。少了这步，
       `?q=%00` 就是一个 500。
    2. **双写单引号**包成字符串字面量。FTS5 查询串有自己的语法，直接把
       用户输入当查询丢进去会抛 OperationalError——实测 '"unterminated'、
       'a OR'、"foo'bar" 三种都抛。后果是「搜一下就把搜索接口打成 500」，
       而且 500 看起来与输入有关，用户只会以为自己输错了。
       FTS5 字符串字面量的转义是**双写单引号**，不是反斜杠。
    """
    return '"' + q.translate(_SEARCH_STRIP_CHARS).replace('"', '""') + '"'


def search_community_videos(q: str = "", page: int = 1,
                           page_size: int = COMMUNITY_PAGE_SIZE_DEFAULT,
                           tag: str = "") -> dict:
    """社区搜索：视频名称关键词 / 视频链接精确定位 / 标签，三种入口。

    - q 以 http(s):// 开头 → 按 video_url **精确**匹配。链接定位要的是「就是
      这一条」，而 FTS 只能在原文里找子串，搜到的是「包含这段链接的东西」，
      两者不是一回事。URL 规范化由前端负责（它已有 canonicalUrl）。
    - 其余 q → FTS5 MATCH，索引覆盖标题、标签、链接三个字段。
    - tag → json_each 精确筛选，可与 q 取交集。

    调用方**必须已鉴权**：搜索是登录用户的能力，未登录返回 401（路由层
    的 Depends 负责），这里只管检索。
    """
    q = (q or "").strip()
    tag_clause, tag_params = _tag_clause(tag)

    if q.startswith(("http://", "https://")):
        where = f"{_COMMUNITY_VISIBLE} AND v.video_url = ?{tag_clause}"
        order = "v.created_at DESC, v.id DESC"
        result = _paginate("videos v", where, (q, *tag_params), page, page_size, order)
    elif q:
        phrase = _fts_phrase(q)
        where = f"{_VIDEO_SEARCH_INDEX} MATCH ? AND {_COMMUNITY_VISIBLE}{tag_clause}"
        # 相关度优先，其次按时间倒序——同分时让新内容在前，顺序稳定可测。
        #
        # bm25() 的参数必须写 FTS 表**名**，不能写 FROM 子句里的别名：
        # 写成 bm25(f) 会报 no such column: f（SQLite 认不出这是个辅助函数，
        # 就当成列引用去找）。MATCH 左侧同理，所以 FROM 里给了别名 f、
        # 条件里仍写表名——这个不一致是实测逼出来的，不是笔误。
        result = _paginate(
            f"{_VIDEO_SEARCH_INDEX} f JOIN videos v ON v.id = f.rowid",
            where, (phrase, *tag_params), page, page_size,
            f"bm25({_VIDEO_SEARCH_INDEX}), v.created_at DESC, v.id DESC",
        )
    else:
        where = f"{_COMMUNITY_VISIBLE}{tag_clause}"
        result = _paginate("videos v", where, tag_params, page, page_size,
                           "v.created_at DESC, v.id DESC")

    result["items"] = [_project_video(r, COMMUNITY_CARD_FIELDS) for r in result["items"]]
    result["mode"] = "url" if q.startswith(("http://", "https://")) else ("text" if q else "browse")
    # 短查询的真相说给前端听：text 模式下不足 MIN_FTS_TERM_CHARS 个字符
    # **必然**召回 0（trigram 不产生这种长度的滑窗），接口照常 200。
    # 不把这个边界讲出来的话，用户搜「机器」看到的就是「没反应」——
    # 与「社区里真没有」在界面上完全一样。URL 精确匹配与浏览模式不受
    # 这个限制（前者走等值、后者不过滤），所以只在 text 模式置位。
    result["min_chars"] = MIN_FTS_TERM_CHARS
    result["q_too_short"] = result["mode"] == "text" and len(q) < MIN_FTS_TERM_CHARS
    return result


# ── 用户操作 ──────────────────────────────────────────────

def get_user_by_email(email: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(email: str, password_hash: str) -> dict:
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash),
        )
        return {"id": cursor.lastrowid, "email": email}


# ── AI 总结次数限制 ───────────────────────────────────────

def is_vip_active(user) -> bool:
    """VIP 权益判定——本项目唯一一份实现。

    naive datetime 兜底在此收口：所有 vip_expire_at 的比较都必须走这里，
    否则同一个"还是不是 VIP"的问题会在多处得到不同答案。
    """
    if not user["is_vip"] or not user["vip_expire_at"]:
        return False
    try:
        expire = datetime.fromisoformat(user["vip_expire_at"])
    except (ValueError, TypeError):
        return False
    # 兜底：SQLite 里存的可能是 naive datetime，统一按 UTC 处理
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=timezone.utc)
    return expire > datetime.now(timezone.utc)


# ── 额度拆分：解析额度 / 对话额度（工单 #4）────────────────
#
# 拆分前那套「单一共用额度」的实现（check_summary_quota /
# consume_summary_quota）已随路由层切换一并删除：全仓库再无调用者，
# 留着只会让人以为还有第二条计费路径。这里是唯一的计费实现。


def _quota_spec(kind: str) -> tuple[str, str, str]:
    """取出某类额度对应的 (计数字段, 日期字段, 上限常量名)。

    拼错的 kind 立刻抛错：静默 fallback 会让「扣了对话额度却记到解析头上」
    这类错误一路走到用户面前才发现。
    """
    spec = _QUOTA_KINDS.get(kind)
    if spec is None:
        raise ValueError(
            f"未知的额度类型 {kind!r}；可用：{sorted(_QUOTA_KINDS)}"
        )
    return spec


def quota_limit(kind: str) -> int:
    """按模块当前值取上限——而不是导入时冻结的常量。

    刻意不写成 `from DAILY_PARSE_LIMIT`：测试会 monkeypatch 模块属性，
    导入时冻结会让「改配置后行为随之改变」这条 AC 无法验证。

    公开是因为路由层要报同样的数字。import 时冻结的副本会和这里的
    remaining 打架，同一份 payload 报出 `remaining=0, limit=3`（真实上限 1）。
    """
    return globals()[_quota_spec(kind)[2]]


def check_quota_kind(user_id: int, kind: str) -> tuple[bool, int]:
    """判定单类额度。只读，不写库。返回 (allowed, remaining)，-1 表示无限。"""
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return False, 0
        if is_vip_active(user):
            return True, -1
        # 日期不是今天，说明今天还没用过，额度是满的
        if user[date_col] != today:
            return True, limit

        current = user[count_col] or 0
        if current >= limit:
            return False, 0
        return True, limit - current


def check_quota(user_id: int) -> dict:
    """一次判定全部额度。键固定为 parse / chat。"""
    return {kind: check_quota_kind(user_id, kind) for kind in _QUOTA_KINDS}


def consume_quota(user_id: int, kind: str) -> int:
    """扣减一次额度，返回扣减后的 remaining。调用前须已通过 check_quota_kind。"""
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return 0
        if is_vip_active(user):
            return -1

        if user[date_col] != today:
            conn.execute(
                f"UPDATE users SET {count_col} = 1, {date_col} = ? WHERE id = ?",
                (today, user_id),
            )
            return limit - 1

        current = user[count_col] or 0
        conn.execute(
            f"UPDATE users SET {count_col} = {count_col} + 1 WHERE id = ?",
            (user_id,),
        )
        return limit - current - 1


def refund_quota(user_id: int, kind: str) -> int:
    """把一次扣减还回去，返回回滚后的 remaining。

    用途：模型调用抛异常时不让用户白扣。扣减与调用之间没有事务，
    所以回滚本身是幂等且原子的（单条 UPDATE，不做读-改-写）：
    绝不会把计数压到负数，也不会跨天给今天白送额度，
    更不会把并发的另一次扣减覆盖掉。
    """
    count_col, date_col, _ = _quota_spec(kind)
    limit = quota_limit(kind)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return 0
        if is_vip_active(user):
            return -1
        # 跨天了：扣的是昨天的，回滚会让今天的额度凭空多出来，不做。
        if user[date_col] != today:
            return limit

        # 单条 UPDATE 里完成减一。读-改-写两步会和并发的 consume_quota
        # 交叉：中间那次扣减会被本处的绝对值覆盖掉，等于白送额度。
        conn.execute(
            f"UPDATE users SET {count_col} = MAX(COALESCE({count_col}, 0) - 1, 0) "
            f"WHERE id = ?",
            (user_id,),
        )
        row = conn.execute(
            f"SELECT {count_col} FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return limit - (row[count_col] or 0)


# ── 订单操作 ──────────────────────────────────────────────

def create_order(user_id: int, order_no: str, amount: int, currency: str = "cny", plan_type: str = "monthly"):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO orders (order_no, user_id, amount, currency, plan_type)
               VALUES (?, ?, ?, ?, ?)""",
            (order_no, user_id, amount, currency, plan_type),
        )


def update_order_stripe_session(order_no: str, session_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE orders SET stripe_session_id = ? WHERE order_no = ?",
            (session_id, order_no),
        )


def complete_order(session_id: str, payment_intent_id: str) -> dict | None:
    """支付完成时更新订单状态、激活 VIP。幂等性：只有 pending 状态的订单才会被处理。"""
    from dateutil.relativedelta import relativedelta

    with get_db() as conn:
        order = conn.execute(
            "SELECT * FROM orders WHERE stripe_session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchone()

        if not order:
            return None

        now = datetime.now(timezone.utc).isoformat()
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (order["user_id"],)
        ).fetchone()

        current_expire = None
        if user["vip_expire_at"]:
            try:
                current_expire = datetime.fromisoformat(user["vip_expire_at"])
                # 兜底 naive datetime（与 is_vip_active 同源修复）
                if current_expire.tzinfo is None:
                    current_expire = current_expire.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        base_time = datetime.now(timezone.utc)
        if current_expire and current_expire > base_time:
            base_time = current_expire

        if order["plan_type"] == "monthly":
            new_expire = base_time + relativedelta(months=1)
        elif order["plan_type"] == "yearly":
            new_expire = base_time + relativedelta(years=1)
        else:
            new_expire = base_time + relativedelta(months=1)

        conn.execute(
            "UPDATE orders SET status='paid', stripe_payment_intent_id=?, paid_at=?, updated_at=? WHERE id=?",
            (payment_intent_id, now, now, order["id"]),
        )
        conn.execute(
            "UPDATE users SET is_vip=1, vip_expire_at=?, updated_at=? WHERE id=?",
            (new_expire.isoformat(), now, order["user_id"]),
        )

        return dict(order)


def get_user_orders(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── 解析历史记录 ──────────────────────────────────────────

def _trim_parse_history(conn, user_id: int):
    """每用户只保留最近 MAX_PARSE_HISTORY_PER_USER 条记录"""
    conn.execute(
        f"""DELETE FROM parse_history WHERE user_id = ? AND id NOT IN (
            SELECT id FROM parse_history WHERE user_id = ?
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT {int(MAX_PARSE_HISTORY_PER_USER)})""",
        (user_id, user_id),
    )


def upsert_parse_history(
    user_id: int,
    video_url: str,
    video_title: str = "",
    video_data: dict | None = None,
    summary_md: str = "",
    mindmap_md: str = "",
    subtitle_data: dict | None = None,
) -> int:
    """按 (user_id, video_url) 去重保存解析历史，返回记录 ID。

    合并语义：新值为空时保留旧值（视频源保存不覆盖 AI 结果，反之亦然），
    非空新值覆盖旧值。查询走 idx_history_user_url 复合索引。
    """
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, video_title, video_data, summary_md, mindmap_md, subtitle_data"
            " FROM parse_history WHERE user_id = ? AND video_url = ?",
            (user_id, video_url),
        ).fetchone()

        def _merge(old_json: str, new_obj: dict | None) -> str:
            new_json = json.dumps(new_obj, ensure_ascii=False) if new_obj else ""
            return new_json or (old_json or "")

        def _merge_text(old: str, new: str) -> str:
            return new or (old or "")

        if row:
            conn.execute(
                """UPDATE parse_history
                   SET video_title=?, video_data=?, summary_md=?, mindmap_md=?,
                       subtitle_data=?, updated_at=?
                   WHERE id=?""",
                (
                    _merge_text(row["video_title"], video_title),
                    _merge(row["video_data"], video_data),
                    _merge_text(row["summary_md"], summary_md),
                    _merge_text(row["mindmap_md"], mindmap_md),
                    _merge(row["subtitle_data"], subtitle_data),
                    now, row["id"],
                ),
            )
            history_id = row["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO parse_history
                   (user_id, video_url, video_title, video_data, summary_md,
                    mindmap_md, subtitle_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, video_url, video_title,
                 json.dumps(video_data, ensure_ascii=False) if video_data else "",
                 summary_md, mindmap_md,
                 json.dumps(subtitle_data, ensure_ascii=False) if subtitle_data else "",
                 now, now),
            )
            history_id = cursor.lastrowid
        _trim_parse_history(conn, user_id)
        return history_id


# ── 追问会话（工单 #8）────────────────────────────────────
#
# 真相源唯一：只有服务端在答案**完整产出后**才调 append_chat_turn。
# 前端不再回调任何保存接口——记录存不存在取决于服务端有没有真答出这句话，
# 而不是取决于浏览器有没有多发一个请求（改一行 JS、或写库前断流，
# 记录都会不见；反过来伪造一条 {question, answer} 也只是一行请求的事）。
#
# 隔离在查询条件里：每个读函数都以 user_id 打头，别人的记录读不出来。


def append_chat_turn(user_id: int, video_url: str, question: str, answer: str) -> None:
    """把一整轮追问（一问 + 一答）写进会话表。

    两行在**同一个事务**里写：只有问没有答的半轮记录配不出 answer，
    分两次写等于在两次写之间留一个窗口，而那个窗口里的会话读出来是残的。
    """
    with get_db() as conn:
        for role, content in (("user", question), ("assistant", answer)):
            conn.execute(
                """INSERT INTO chat_messages (user_id, video_url, role, content)
                   VALUES (?, ?, ?, ?)""",
                (user_id, video_url, role, content),
            )


def get_recent_chat_messages(user_id: int, video_url: str, turns: int | None = 3) -> list:
    """某个用户与某个视频最近的追问明细，按 id **升序**返回。

    `turns` 轮 = 2 * turns 条（写入永远成对）；`turns=None` 取全部，
    聚合整段会话时用。升序是拼 prompt 的要求：时间序给模型才读得通。
    取「最近的一段」却在 SQL 里正序查会拿到最早的，所以先倒序取再翻回来。
    """
    sql = ("SELECT role, content FROM chat_messages"
           " WHERE user_id = ? AND video_url = ? ORDER BY id DESC")
    params = [user_id, video_url]
    if turns is not None:
        sql += " LIMIT ?"
        params.append(max(0, int(turns)) * 2)
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _pair_up_chat_messages(messages: list) -> list:
    """把消息明细配成 [{question, answer}]。

    相邻的 user / assistant 两条配成一轮。末尾若剩一个孤立的 user 行
    （有问无答），**丢弃**：半轮对话展示成一条空回答比不展示更糟。
    """
    turns: list = []
    pending: str | None = None
    for msg in messages:
        if msg["role"] == "user":
            # 新的问题开始。上一轮若没配上答案（脏数据），就此丢弃，
            # 不让它顺延到这一轮上把两问配成一次回答。
            pending = msg["content"]
        elif msg["role"] == "assistant" and pending is not None:
            turns.append({"question": pending, "answer": msg["content"]})
            pending = None
    return turns


def get_chat_session(user_id: int, video_url: str) -> list:
    """某个用户与某个视频的完整会话，形状恒为 [{question, answer}]。

    **所有读点都必须走这里**，别再自己拼一份：新表为空时回退读旧列这条
    规则散在两处就一定会漂移（详情接口给老记录、新端点给空）。
    """
    turns = _pair_up_chat_messages(get_recent_chat_messages(user_id, video_url, turns=None))
    if turns:
        return turns
    # 下面这条查询**同样**按 user_id 过滤：旧列挂在 parse_history 行上，
    # 漏掉它就是别人的追问记录被读出来。
    with get_db() as conn:
        row = conn.execute(
            "SELECT chat_history FROM parse_history WHERE user_id = ? AND video_url = ?",
            (user_id, video_url),
        ).fetchone()
    return _legacy_chat_history(row["chat_history"]) if row else []


def _legacy_chat_history(raw: str | None) -> list:
    """读老库里的 parse_history.chat_history（工单 #8 之前唯一的存放处）。

    **为什么还留着这条路**：老库里已有的记录还躺在那一列，尚未迁移；
    新表为空时回退读它，那些历史才不至于凭空消失。旧列从此只读不写。
    """
    try:
        chats = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    return chats if isinstance(chats, list) else []


def get_parse_histories(user_id: int, limit: int = MAX_PARSE_HISTORY_PER_USER) -> list:
    """历史记录列表（不含大字段，含摘要预览）"""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, video_url, video_title, summary_md,
                      COALESCE(updated_at, created_at) AS updated_at,
                      created_at,
                      -- 封面地址供历史列表渲染缩略图。video_data 本身是
                      -- 大字段，列表页不需要，所以只把里面那一项用
                      -- json_extract 挑出来，不整块拉出来再丢。
                      --
                      -- json_valid 是**必需**的守卫，不是保险：实测（.scratch/
                      -- probe_json_extract.py）一行非法 JSON 就会让整条查询
                      -- 抛 "malformed JSON"，也就是**一条坏记录足以让整个
                      -- 历史列表 500**。取不到就返回空串，前端回落到占位图标。
                      CASE WHEN json_valid(video_data)
                           THEN COALESCE(json_extract(video_data, '$.thumbnail'), '')
                           ELSE '' END AS cover_url,
                      -- 追问记录在 chat_messages（工单 #8）。
                      -- 后面 OR 的是老库那一列：新表为空时它仍然是唯一有记录的地方，
                      -- 漏掉它会让「详情里读得到记录、列表里却没有」自相矛盾。
                      (EXISTS (SELECT 1 FROM chat_messages m
                               WHERE m.user_id = parse_history.user_id
                                 AND m.video_url = parse_history.video_url)
                       OR (chat_history IS NOT NULL AND chat_history != '[]')) AS has_chat
               FROM parse_history WHERE user_id = ?
               ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            has_chat = bool(item.get("has_chat"))
            summary_md = item.pop("summary_md") or ""
            item["summary_preview"] = summary_md.strip()[:120]
            item["has_chat"] = has_chat
            item["has_ai_result"] = has_chat or bool(item["summary_preview"])
            # cover_url 由上面的 json_valid 守卫保证是字符串；
            # 这里只做一次类型归一，前端不必再判 null。
            if not isinstance(item.get("cover_url"), str):
                item["cover_url"] = ""
            items.append(item)
        return items


def get_parse_history_detail(user_id: int, history_id: int) -> dict | None:
    """单条历史记录完整内容（JSON 字段已反序列化）"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM parse_history WHERE user_id = ? AND id = ?",
            (user_id, history_id),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        for field in ("video_data", "subtitle_data"):
            try:
                item[field] = json.loads(item[field]) if item[field] else None
            except (ValueError, TypeError):
                item[field] = None

    # 读在 with 块**外面**做，与那个事务彻底分开。会话读法只有一个入口，
    # 老列回退也归它管——这里不再另写一份。
    item["chat_history"] = get_chat_session(user_id, item["video_url"])
    return item


def delete_parse_history(user_id: int, history_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM parse_history WHERE user_id = ? AND id = ?",
            (user_id, history_id),
        )
        return cursor.rowcount > 0


# ── 社区视频（工单 #6 / ADR 0001）────────────────────────
#
# 这一族函数就是「同一个链接全站只解析一次」的机制本体：
#
#   reserve_video    抢占解析权（唯一索引在这里真正起作用）
#   complete_video   占位者把结果写回自己占的那一行
#   release_video    占位者失败时把位置还回去
#   get_video_by_url 读社区里已有的那一份结果
#
# 为什么必须是「先占位」而不是「先查有没有、没有就插入」：
# 查与插之间有一个窗口，两个用户同时进来会双双查到「还没有」，
# 于是双双去调模型、双双扣额度——而此时两人都还什么结果都没拿到。
# 唯一索引只能把第二次**插入**挡下来，挡不住第二次**调模型**。
# 所以本设计把「调模型」这个动作挂在 reserve 成功这个条件上：
# 抢到占位的人去调模型，没抢到的人转去等待或复用。
# 这三件事（一次模型调用、一次额度扣减、一行数据）因此同时成立。
#
# 唯一的例外是「占位者自己失败」：它不能永远占着那个位置，
# 否则这个链接从此再也没人能解析——所以 finally 里必须 release_video。

#: 占位行的状态。pending = 已有人占位、正在解析（结果还没产出）；
#: ready = 社区里已有结果，谁来读都是同一份，谁都不能改。
VIDEO_STATUS_PENDING = "pending"
VIDEO_STATUS_READY = "ready"

#: 陈旧占位的回收阈值（秒）。占位者只在 `finally` 里还位——进程被杀、
#: 机器断电、模型无限挂起时它跑不到那里，那一行就永远停在 pending，
#: **这个链接从此再也没人能解析**。所以这里给占位加一个上限。
#:
#: ⚠️ 这个值必须**大于一次正常解析的最长耗时**（字幕提取 + 一次模型调用）。
#: 调小它会在占位者还在好好干活时把位置偷走，于是两个人同时调模型、
#: 同时扣额度——正好是本设计要防的那件事。它是「可恢复性」与
#: 「不重复扣费」之间的取舍，取后者优先。
VIDEO_PENDING_TTL_SECONDS = _env_int("VIDDIGEST_VIDEO_PENDING_TTL_SECONDS", 1800)


def _pending_is_stale(row: dict) -> bool:
    """占位行是否已经老到可以安全接管。解析不了时间就当它还活着。"""
    raw = row.get("updated_at")
    if not raw:
        return False
    try:
        updated = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age > VIDEO_PENDING_TTL_SECONDS


def reserve_video(video_url: str, user_id: int | None) -> tuple[str, dict | None]:
    """抢占一个链接的解析权。返回 (outcome, row)。

    outcome 只有三种，调用方必须分别处理——尤其是 "reserved"：
    **只有拿到它的人才允许去调模型**，这是「只调一次模型」的唯一入口。

    - "reserved"  占位成功，调用者是首次解析者
    - "ready"     社区里已有结果，row 就是那份结果，直接复用
    - "pending"   别人正在解析，调用方需要等它出结果

    写与读刻意分在两个事务里：插入被唯一索引拒绝之后，必须在一个
    **新的**事务里重读，否则读到的还是那个已被回滚的事务的快照。
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO videos
                   (video_url, status, parsed_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (video_url, VIDEO_STATUS_PENDING, user_id, now, now),
            )
        return "reserved", None
    except sqlite3.IntegrityError:
        # 唯一索引挡住了重复行：这个链接已有人在解析或已解析完成。
        row = get_video_by_url(video_url)
        if row is None:
            # 占位者刚失败并把位置还了回去。这里**不能**当成 reserved——
            # 报 pending 让调用方重试一轮，下一轮就能抢到。
            return "pending", None
        if row["status"] == VIDEO_STATUS_READY:
            return "ready", row

        # 别人占着位，但那个占位可能早就死了（进程被杀、模型挂死）——
        # 不回收的话这个链接就永久不可解析。条件写进 WHERE，保证并发下
        # 只有一个人接管成功；抢输的人下一轮再试。
        if _pending_is_stale(row):
            now = datetime.now(timezone.utc).isoformat()
            with get_db() as conn:
                cursor = conn.execute(
                    """UPDATE videos SET parsed_by = ?, created_at = ?, updated_at = ?
                       WHERE video_url = ? AND status = ? AND updated_at = ?""",
                    (user_id, now, now, video_url,
                     VIDEO_STATUS_PENDING, row["updated_at"]),
                )
                if cursor.rowcount == 1:
                    return "reserved", None
        return "pending", row


def complete_video(
    video_url: str,
    summary_md: str = "",
    mindmap_md: str = "",
    tags: list | None = None,
    subtitle_text: str = "",
) -> int:
    """占位者把解析结果写回**自己占的那一行**，返回更新的行数。

    ``WHERE status = 'pending'`` 不是多余的防御：它是「社区内容不会被
    任何人改写」这条承诺的落地点——已 ready 的行谁都改不了，包括
    后来的解析者。返回 0 说明这一行已经不是 pending（正常流程下不会
    发生），由调用方报警而不是静默当作成功。
    """
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(list(tags) if tags else [], ensure_ascii=False)
    with get_db() as conn:
        cursor = conn.execute(
            """UPDATE videos
               SET status = ?, summary_md = ?, mindmap_md = ?, tags = ?,
                   subtitle_text = ?, updated_at = ?
               WHERE video_url = ? AND status = ?""",
            (
                VIDEO_STATUS_READY, summary_md, mindmap_md, payload,
                subtitle_text, now, video_url, VIDEO_STATUS_PENDING,
            ),
        )
        return cursor.rowcount


def release_video(video_url: str) -> int:
    """占位者失败时把位置还回去，返回删掉的行数。

    只删 pending 行：已经 ready 的社区内容与占位无关，绝不能被回滚删掉。

    不还回去的代价是永久性的——那一行会永远停在 pending，
    后来的每个用户都只能干等到超时，再也没人能解析这个链接。
    """
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM videos WHERE video_url = ? AND status = ?",
            (video_url, VIDEO_STATUS_PENDING),
        )
        return cursor.rowcount


def get_video_by_url(video_url: str) -> dict | None:
    """读社区里那一份结果（tags 已反序列化成列表）。没有则 None。

    这是「视频是否已存在」「取某视频的字幕」唯一的查询入口——
    不再查 parse_history（ADR 0001）：那张表是按 (user_id, video_url)
    去重的个人记录，跨用户看不见别人的解析结果。
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE video_url = ?", (video_url,)
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        parsed = json.loads(item.get("tags") or "[]")
    except (ValueError, TypeError):
        parsed = []
    # 类型不对就当没有，不让脏数据变成下游的 TypeError
    item["tags"] = [t for t in parsed if isinstance(t, str)] if isinstance(parsed, list) else []
    return item
