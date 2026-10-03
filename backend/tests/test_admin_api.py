"""管理后台 API（工单 #12）：四个端点的鉴权、读出口、写出口、值域。

三条硬规矩，本文件逐条遵守（沿用 test_admin_auth.py 的纪律）：

1. **走真实 HTTP 层**（seams.make_client），且**不触发 lifespan**。
   不用 `with client:`——那会跑 init_db()，把迁移类断言遮住
   （工单 #7 的假护栏就是这么来的）。本文件全部用 db 夹具建好库，
   探针直接打路由。

2. **每条安全断言都有正反双向对照**。只测「非管理员 403」是不够的——
   「所有请求都 401/403」同样能让它绿。所以同一个文件里必然还有
   「管理员 200 且数据非空」。

3. **断言具体值，不是只断言 200**。返回空数组的「成功」不是成功：
   凡是读出口，都断言拿到了**这个**账号 / **这条**视频的那一行。

外加一条本工单特有的：**响应体里不许出现任何 key / token / 凭据字段**
（ADR 0004 / 0011）。这一条要真的递归扫响应体，不是只断言 200。
"""
from datetime import datetime, timedelta, timezone

import sqlite3

import pytest

import admin_api
import auth
import database
from seams import auth_headers, make_client

#: 递归扫响应体时，任何一个键命中这些片段就算违规。
#: password_hash 也在这里：users 表里确实有这一列，白名单漏掉它
#: 就是把散列发给浏览器（JWT_SECRET 都能被猜，密码散列同理）。
_CREDENTIAL_HINTS = ("key", "token", "secret", "password", "credential", "api_key")


def _assert_no_credentials(payload, where: str) -> None:
    """递归断言：整个响应体里没有任何凭据形状的键。"""
    if isinstance(payload, dict):
        for name, value in payload.items():
            lowered = name.lower()
            assert not any(h in lowered for h in _CREDENTIAL_HINTS), (
                f"{where} 响应体里出现了凭据形状的键 {name!r}"
            )
            _assert_no_credentials(value, where)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_credentials(item, where)


def _set_admin(user_id: int, value: int = 1) -> None:
    with database.get_db() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (value, user_id))


def _set_vip(user_id: int, days: int = 30) -> None:
    expire = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with database.get_db() as conn:
        conn.execute(
            "UPDATE users SET is_vip = 1, vip_expire_at = ? WHERE id = ?",
            (expire, user_id),
        )


def _set_override(user_id: int, parse=None, chat=None) -> None:
    with database.get_db() as conn:
        conn.execute(
            "UPDATE users SET parse_limit_override = ?, chat_limit_override = ? "
            "WHERE id = ?", (parse, chat, user_id),
        )


@pytest.fixture()
def client_app(db, make_user):
    """真实 app + 真实 client + 一个管理员、一个普通用户。

    刻意不 `with client`：见文件头第 1 条。lifespan 里的 init_db() 会
    把「迁移有没有真的跑」这件事遮掉。
    """
    import main as main_module

    admin_id = make_user(email="boss@example.com")
    plain_id = make_user(email="plain@example.com")
    _set_admin(admin_id)
    return make_client(main_module.app), admin_id, plain_id


def _tok(user_id: int, email: str) -> dict:
    return auth_headers(auth.create_token(user_id, email))


def _admin_hdr(client_app) -> dict:
    """管理员的 Authorization 头。普通函数而不是 fixture。

    写成 fixture 就得给每条测试都加一个参数，而这里的每条测试本来就
    已经有 client_app（它们都要拿 client），多加一个只是噪音。
    """
    _, admin_id, _ = client_app
    return _tok(admin_id, "boss@example.com")


def _plain_hdr(client_app) -> dict:
    _, _, plain_id = client_app
    return _tok(plain_id, "plain@example.com")


ADMIN_GET_PATHS = [
    "/api/admin/users",
    "/api/admin/models",
    "/api/admin/community",
]


# ── 鉴权：正反双向（安全边界）───────────────────────────────

@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
def test_admin_get_endpoints_reject_non_admin_with_403(client_app, path):
    client, _, _ = client_app
    r = client.get(path, headers=_plain_hdr(client_app))
    assert r.status_code == 403, f"非管理员调 {path} 应得 403，实得 {r.status_code}：{r.text}"


def test_admin_post_quota_rejects_non_admin_with_403(client_app, make_user):
    client, _, _ = client_app
    victim = make_user(email="victim@example.com")
    r = client.post(
        f"/api/admin/users/{victim}/quota",
        json={"parse_limit": 9},
        headers=_plain_hdr(client_app),
    )
    assert r.status_code == 403, f"实得 {r.status_code}：{r.text}"
    # 反向确认：403 的请求**没有**把额度改掉（鉴权失败不等于「改了但报错」）
    assert database.check_quota_kind(victim, "parse")[1] == database.DAILY_PARSE_LIMIT


@pytest.mark.parametrize("path", ADMIN_GET_PATHS)
def test_admin_get_endpoints_reject_missing_token_with_401(client_app, path):
    client, _, _ = client_app
    r = client.get(path)
    assert r.status_code == 401, (
        f"没带 token 调 {path} 应得 401（没证明身份），实得 {r.status_code}"
    )
    assert r.status_code != 403, "401 与 403 含义不同，混了会误导前端"


def test_admin_get_endpoints_allow_admin_with_non_empty_data(client_app, db):
    """反向对照：同一个 token、同一个路径，管理员必须 200 且**有数据**。

    这一条是防「全员 403 也能让测试绿」的那一半。只写它不够
    （返回空数组也能绿），所以上面三条 403 断言仍然必须都在。
    """
    client, _, _ = client_app
    # 社区列表要有一条真行，否则空库启动时它合法地就是空的。
    database.reserve_video("https://v.example/1", 1)
    for path in ADMIN_GET_PATHS:
        r = client.get(path, headers=_admin_hdr(client_app))
        assert r.status_code == 200, f"管理员调 {path} 应得 200，实得 {r.status_code}：{r.text}"
        body = r.json()
        assert body["items"], f"{path} 返回了空数组——那不是成功"


def test_admin_post_quota_allows_admin(client_app, make_user):
    client, _, _ = client_app
    victim = make_user(email="victim@example.com")
    r = client.post(
        f"/api/admin/users/{victim}/quota",
        json={"parse_limit": 7},
        headers=_admin_hdr(client_app),
    )
    assert r.status_code == 200, f"实得 {r.status_code}：{r.text}"
    assert r.json()["user"]["parse_limit"] == 7


def test_public_models_needs_no_auth_at_all(client_app):
    """`/api/models` 是公开的：不带任何头也必须能拿到非空清单。

    挂成 require_admin 的话，普通用户的厂商下拉就没了——
    那是「消除漂移」变成「功能消失」，不是收敛。
    """
    client, _, _ = client_app
    r = client.get("/api/models")
    # 先断状态码再碰 body：直接 r.json()["items"] 的话，
    # 端点被挂上鉴权这类变异会以 KeyError 崩掉，而不是一条断言变红。
    assert r.status_code == 200, f"公开端点不应鉴权，实得 {r.status_code}：{r.text}"
    assert r.json()["items"], "公开清单返回空数组"


def test_public_models_is_identical_for_anonymous_and_admin(client_app):
    """登录与否返回**完全相同**的形状——所以这里不挂任何鉴权依赖。"""
    client, _, _ = client_app
    anonymous = client.get("/api/models").json()
    as_admin = client.get("/api/models", headers=_admin_hdr(client_app)).json()
    assert anonymous == as_admin


# ── 读出口：用户记录 ────────────────────────────────────────

def test_admin_users_returns_the_real_row_with_effective_limits(client_app, make_user):
    client, _, _ = client_app
    target = make_user(email="reader@example.com")

    r = client.get("/api/admin/users", headers=_admin_hdr(client_app))
    assert r.status_code == 200
    body = r.json()
    # boss + plain + reader 三个
    assert body["total"] == 3, body

    row = next(i for i in body["items"] if i["id"] == target)
    assert row["email"] == "reader@example.com"
    assert row["is_admin"] == 0
    # 生效中的额度：没设过覆盖值 → 回落全局，且 source 说得出它从哪来
    assert row["parse_limit"] == database.DAILY_PARSE_LIMIT
    assert row["parse_limit_source"] == "global"
    assert row["parse_limit_override"] is None
    assert row["chat_limit"] == database.DAILY_CHAT_LIMIT
    assert row["chat_limit_source"] == "global"
    assert row["parse_used"] == 0 and row["chat_used"] == 0
    assert row["created_at"], "注册时间不该为空"
    assert set(row) == {
        "id", "email", "is_admin", "is_vip", "vip_expire_at", "created_at",
        "parse_used", "chat_used", "parse_limit", "chat_limit",
        "parse_limit_override", "chat_limit_override",
        "parse_limit_source", "chat_limit_source",
    }, f"字段集与前端契约不符，多了或少了：{sorted(row)}"


def test_admin_users_shows_override_as_its_own_source(client_app, make_user):
    client, _, _ = client_app
    target = make_user(email="limited@example.com")
    _set_override(target, parse=4, chat=None)

    row = next(i for i in client.get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
               if i["id"] == target)
    assert row["parse_limit"] == 4
    assert row["parse_limit_source"] == "override"
    assert row["parse_limit_override"] == 4
    # chat 没设 → 仍是全局，两项互不干扰
    assert row["chat_limit"] == database.DAILY_CHAT_LIMIT
    assert row["chat_limit_source"] == "global"
    assert row["chat_limit_override"] is None


def test_admin_users_reports_vip_as_its_own_source(client_app, make_user):
    """VIP 生效时 source 必须是 "vip"——后台显示的额度要和真实判定一致。"""
    client, _, _ = client_app
    target = make_user(email="vip@example.com")
    _set_vip(target)

    row = next(i for i in client.get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
               if i["id"] == target)
    assert row["parse_limit_source"] == "vip"
    assert row["chat_limit_source"] == "vip"
    assert row["parse_limit"] == database.QUOTA_UNLIMITED


def test_admin_users_counts_only_todays_usage(client_app, make_user):
    """昨天用掉的 3 次，今天不该还被算成「已用 3 次」。

    不做这一步的话，第二天早上后台会显示一个已经不存在的用量，
    管理员会照着它去改额度。
    """
    client, _, _ = client_app
    target = make_user(email="yesterday@example.com")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    with database.get_db() as conn:
        conn.execute(
            "UPDATE users SET daily_parse_count = 3, last_parse_date = ? WHERE id = ?",
            (yesterday, target),
        )

    row = next(i for i in client.get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
               if i["id"] == target)
    assert row["parse_used"] == 0, row


def test_admin_users_pagination_actually_slices(client_app, make_user):
    for i in range(4):
        make_user(email=f"bulk{i}@example.com")

    first = client_app[0].get("/api/admin/users?limit=2&offset=0", headers=_admin_hdr(client_app)).json()
    second = client_app[0].get("/api/admin/users?limit=2&offset=2", headers=_admin_hdr(client_app)).json()

    assert len(first["items"]) == 2 and len(second["items"]) == 2
    assert first["total"] == second["total"] == 6, "total 是命中总数，不随分页变"
    assert first["limit"] == 2 and first["offset"] == 0
    assert second["offset"] == 2
    first_ids = {i["id"] for i in first["items"]}
    second_ids = {i["id"] for i in second["items"]}
    assert not (first_ids & second_ids), "两页重叠了——offset 没真的切片"


def test_admin_users_limit_is_clamped_not_honoured_verbatim(client_app, make_user):
    """limit 有上界：一次请求拉不完 200+ 条（防「一次拉全表」）。"""
    for i in range(database.ADMIN_PAGE_SIZE_MAX + 10):
        make_user(email=f"many{i}@example.com")

    body = client_app[0].get("/api/admin/users?limit=100000", headers=_admin_hdr(client_app)).json()
    assert body["limit"] == database.ADMIN_PAGE_SIZE_MAX, (
        f"limit 没有被收敛到上界，实得 {body['limit']}"
    )
    assert len(body["items"]) == database.ADMIN_PAGE_SIZE_MAX
    assert body["total"] > database.ADMIN_PAGE_SIZE_MAX, (
        "库里明明比一页多，total 必须如实报出来"
    )


def test_admin_users_q_filters_by_email_substring(client_app, make_user):
    make_user(email="apple@fruit.com")

    hit = client_app[0].get("/api/admin/users?q=apple", headers=_admin_hdr(client_app)).json()
    assert [i["email"] for i in hit["items"]] == ["apple@fruit.com"]
    assert hit["total"] == 1

    miss = client_app[0].get("/api/admin/users?q=banana", headers=_admin_hdr(client_app)).json()
    assert miss["items"] == [] and miss["total"] == 0


def test_admin_users_q_treats_wildcards_literally(client_app):
    """`%` 与 `_` 必须是普通字符，不当 LIKE 通配符。

    不转义的话，管理员搜一个 `%` 会拿到全表——那不是安全漏洞，
    但足以让人对着「搜什么都全出来」的结果排查很久。
    """
    body = client_app[0].get("/api/admin/users?q=%25", headers=_admin_hdr(client_app)).json()
    assert body["items"] == [] and body["total"] == 0, (
        "% 被当成了通配符：整个邮箱列表都被匹配了"
    )


# ── 读出口：社区记录 ────────────────────────────────────────

def test_admin_community_returns_real_rows_with_author(client_app, make_user):
    client, _, plain_id = client_app
    database.reserve_video("https://v.example/ready", plain_id)
    database.complete_video("https://v.example/ready", summary_md="s", tags=["编程"])

    body = client.get("/api/admin/community", headers=_admin_hdr(client_app)).json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["video_url"] == "https://v.example/ready"
    assert row["author_email"] == "plain@example.com", "作者邮箱要靠 LEFT JOIN users"
    assert row["tags"] == ["编程"], "tags 必须解析成 list，不能是 JSON 字符串"
    assert row["created_at"]
    # status 是 ADR 0013 加的：管理员删之前得分得清这是真内容还是空占位。
    assert set(row) == {"id", "video_url", "title", "author_email", "tags",
                        "created_at", "status"}, f"字段集与契约不符：{sorted(row)}"


def test_admin_community_keeps_placeholder_rows(client_app, make_user):
    """pending 占位行也要看得见——「谁占了位没解析完」正是后台要查的事。"""
    client, _, plain_id = client_app
    database.reserve_video("https://v.example/pending", plain_id)

    body = client.get("/api/admin/community", headers=_admin_hdr(client_app)).json()
    assert body["total"] == 1
    assert body["items"][0]["video_url"] == "https://v.example/pending"


def test_admin_community_keeps_rows_of_deleted_parsers(client_app, make_user):
    """解析者被删后社区行必须留下来（ADR 0010：parsed_by 无外键）。

    用 LEFT JOIN 正是为了这条；写成 INNER JOIN 这些行会凭空消失，
    而它们恰恰是最该被看见的。
    """
    client, _, plain_id = client_app
    database.reserve_video("https://v.example/orphan", plain_id)
    with database.get_db() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (plain_id,))

    body = client.get("/api/admin/community", headers=_admin_hdr(client_app)).json()
    assert body["total"] == 1, "解析者没了不该让社区行一起消失"
    assert body["items"][0]["author_email"] is None


def test_admin_community_paginates(client_app, make_user):
    for i in range(3):
        database.reserve_video(f"https://v.example/{i}", 1)

    body = client_app[0].get("/api/admin/community?limit=2&offset=0", headers=_admin_hdr(client_app)).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 0


# ── 写出口：额度真的落库且真的生效 ──────────────────────────

def _route_quota(user_id: int) -> dict:
    """走真实 HTTP 层读 /api/quota（路由层报 remaining/limit 的那条路）。

    刻意不 `with client`（同文件头第 1 条）：这条路的意义就是读路由层
    报出来的数字，lifespan 跑一遍 init_db 只会让「读到的是谁算的」变模糊。
    """
    import main as main_module

    return make_client(main_module.app).get(
        "/api/quota", headers=_tok(user_id, "someone@example.com")
    ).json()


def test_set_quota_changes_the_next_real_quota_check(client_app, make_user):
    """写完再读一次确认落库，且**下一次真实额度校验**读到的是新值。"""
    client, _, _ = client_app
    target = make_user(email="limited@example.com")

    assert database.check_quota_kind(target, "parse") == (
        True, database.DAILY_PARSE_LIMIT,
    ), "前置：改之前用的是全局值"

    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"parse_limit": 1}, headers=_admin_hdr(client_app))
    assert r.status_code == 200, r.text

    # 数据层：重读库里的行，而不是看返回值
    assert database.get_user_by_id(target)["parse_limit_override"] == 1
    # 真实判定立刻变了
    assert database.check_quota_kind(target, "parse") == (True, 1)
    assert database.consume_quota(target, "parse") == 0
    assert database.check_quota_kind(target, "parse") == (False, 0), (
        "上限改成 1 之后不该还能再用第二次"
    )


def test_all_four_call_sites_agree_on_the_limit(client_app, make_user):
    """四处读出口必须给出**同一个** limit。

    分裂的形态恰恰是「各自都对」：数据层按覆盖值算 remaining，
    路由层拿全局值去报 limit，于是同一份 payload 报出
    `remaining=0, limit=3`（真实上限 1）。任何一处漏传 user_id 都会红。
    """
    client, _, _ = client_app
    target = make_user(email="split@example.com")
    client.post(f"/api/admin/users/{target}/quota",
                json={"parse_limit": 1}, headers=_admin_hdr(client_app))

    assert database.consume_quota(target, "parse") == 0  # 扣光

    from_data_layer = database.quota_limit("parse", target)
    from_refund = database.refund_quota(target, "parse")
    from_route = _route_quota(target)["parse"]

    assert from_data_layer == 1
    assert from_refund == 1
    assert from_route["limit"] == 1, (
        f"路由层报的是 {from_route['limit']}，数据层是 {from_data_layer}——分裂了"
    )
    assert from_route["remaining"] == 1
    assert from_data_layer != database.DAILY_PARSE_LIMIT, (
        "前置失效：覆盖值与全局值相同，测不出分裂"
    )


def test_set_quota_null_clears_override_and_falls_back_to_global(client_app, make_user):
    client, _, _ = client_app
    target = make_user(email="cleared@example.com")
    client.post(f"/api/admin/users/{target}/quota",
                json={"parse_limit": 1}, headers=_admin_hdr(client_app))
    assert database.quota_limit("parse", target) == 1

    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"parse_limit": None}, headers=_admin_hdr(client_app))
    assert r.status_code == 200, r.text

    assert database.get_user_by_id(target)["parse_limit_override"] is None
    assert database.quota_limit("parse", target) == database.DAILY_PARSE_LIMIT
    assert r.json()["user"]["parse_limit_source"] == "global"


def test_set_quota_touches_only_the_named_field(client_app, make_user):
    """请求里没出现的字段**不动**。只改对话额度不该顺手清掉解析额度。"""
    client, _, _ = client_app
    target = make_user(email="partial@example.com")
    _set_override(target, parse=2, chat=3)

    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"chat_limit": 9}, headers=_admin_hdr(client_app))
    assert r.status_code == 200, r.text

    row = database.get_user_by_id(target)
    assert row["parse_limit_override"] == 2, "没传的字段被改了"
    assert row["chat_limit_override"] == 9


def test_set_quota_only_touches_the_named_user(client_app, make_user):
    client, _, _ = client_app
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    client.post(f"/api/admin/users/{a}/quota",
                json={"parse_limit": 1}, headers=_admin_hdr(client_app))

    assert database.quota_limit("parse", b) == database.DAILY_PARSE_LIMIT


def test_override_zero_blocks_everything(client_app, make_user):
    """0 是「一条都不能用」，不是「没设置」。"""
    client, _, _ = client_app
    target = make_user(email="zero@example.com")
    client.post(f"/api/admin/users/{target}/quota",
                json={"parse_limit": 0}, headers=_admin_hdr(client_app))

    assert database.get_user_by_id(target)["parse_limit_override"] == 0
    assert database.check_quota_kind(target, "parse") == (False, 0)
    # 路由层也必须跟着说「不可用」，而不是 remaining=0, limit=3
    assert _route_quota(target)["parse"]["allowed"] is False


def test_override_minus_one_is_unlimited(client_app, make_user):
    """-1 = 无限，沿用 remaining == -1 的既有约定。

    没有这层处理的话，`current >= limit` 会拿 0 >= -1 判成「已用完」，
    「设成无限」的用户反而被彻底封死——比不设更糟。
    """
    client, _, _ = client_app
    target = make_user(email="unlimited@example.com")
    client.post(f"/api/admin/users/{target}/quota",
                json={"parse_limit": -1}, headers=_admin_hdr(client_app))

    assert database.check_quota_kind(target, "parse") == (True, -1)
    for _ in range(5):
        assert database.consume_quota(target, "parse") == -1
    assert database.check_quota_kind(target, "parse") == (True, -1)
    # 计数照记（后台要看今日用量），只是不参与判定。
    # 经后台读出口断言，不调私有函数。
    used = next(
        i for i in client.get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
        if i["id"] == target
    )["parse_used"]
    assert used == 5, "无限额度也要记今日用量，否则后台看不出这个人用过"

    # 回滚同样必须报 -1，且真的把计数减回去（无限不等于不用记账）
    assert database.refund_quota(target, "parse") == -1
    used_after = next(
        i for i in client.get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
        if i["id"] == target
    )["parse_used"]
    assert used_after == 4, used_after


def test_set_quota_on_vip_says_it_will_not_take_effect(client_app, make_user):
    """给有效 VIP 改额度必须明说不生效，不能静默成功。"""
    client, _, _ = client_app
    target = make_user(email="vip@example.com")
    _set_vip(target)

    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"parse_limit": 1}, headers=_admin_hdr(client_app))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["note"] == "vip_not_effective", body
    assert body["message"] == "给有效 VIP 改额度不会生效"
    # 真实判定仍然是无限——这才是「不生效」的证据
    assert database.check_quota_kind(target, "parse") == (True, -1)
    # 值仍然写进去了（VIP 到期后它就该生效）
    assert database.get_user_by_id(target)["parse_limit_override"] == 1


def test_set_quota_on_expired_vip_takes_effect(client_app, make_user):
    client, _, _ = client_app
    target = make_user(email="expired@example.com")
    expire = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with database.get_db() as conn:
        conn.execute(
            "UPDATE users SET is_vip = 1, vip_expire_at = ? WHERE id = ?", (expire, target)
        )

    body = client.post(f"/api/admin/users/{target}/quota",
                       json={"parse_limit": 1}, headers=_admin_hdr(client_app)).json()
    assert body["note"] is None, body
    assert database.check_quota_kind(target, "parse") == (True, 1)


def test_set_quota_on_unknown_user_is_404_not_500(client_app):
    r = client_app[0].post("/api/admin/users/999999/quota",
                           json={"parse_limit": 1}, headers=_admin_hdr(client_app))
    assert r.status_code == 404, f"实得 {r.status_code}：{r.text}"
    assert r.status_code != 500


def test_set_quota_returns_the_reread_row_not_the_request(client_app, make_user):
    """返回值必须是重读库的结果：夹带一个别的字段进去就该看得出来。"""
    client, _, _ = client_app
    target = make_user(email="echo@example.com")
    body = client.post(f"/api/admin/users/{target}/quota",
                       json={"parse_limit": 2}, headers=_admin_hdr(client_app)).json()

    row = body["user"]
    assert row["id"] == target
    assert row["email"] == "echo@example.com"
    assert row["parse_limit"] == 2
    assert row["parse_limit_override"] == 2
    assert row["parse_limit_source"] == "override"
    # 没传的字段原样带回，且来自库里
    assert row["chat_limit"] == database.DAILY_CHAT_LIMIT


# ── 值域：非法值必须 400（契约要求 400，不是 422）──────────

@pytest.mark.parametrize("bad", [-2, -100, 1.5, "3", True, False, [1], {"n": 1},
                                 admin_api.QUOTA_OVERRIDE_MAX + 1])
def test_set_quota_rejects_out_of_domain_values_with_400(client_app, make_user, bad):
    client, _, _ = client_app
    target = make_user(email="bad@example.com")
    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"parse_limit": bad}, headers=_admin_hdr(client_app))
    assert r.status_code == 400, f"值 {bad!r} 应得 400，实得 {r.status_code}：{r.text}"
    assert r.status_code != 422, "值域非法是 400，不是 pydantic 的 422"
    # 拒绝的值不该落库
    assert database.get_user_by_id(target)["parse_limit_override"] is None


@pytest.mark.parametrize("good", [None, 0, -1, 1, admin_api.QUOTA_OVERRIDE_MAX])
def test_set_quota_accepts_every_in_domain_value(client_app, make_user, good):
    client, _, _ = client_app
    target = make_user(email=f"ok{good}@example.com")
    r = client.post(f"/api/admin/users/{target}/quota",
                    json={"parse_limit": good}, headers=_admin_hdr(client_app))
    assert r.status_code == 200, f"值 {good!r} 应被接受，实得 {r.status_code}：{r.text}"


# ── 单元测试：值域收敛（纯函数，不触库）─────────────────────

@pytest.mark.parametrize("raw, expected", [
    (None, None),
    (0, 0),
    (-1, -1),
    (1, 1),
    (100_000, 100_000),
])
def test_normalize_quota_value_accepts(raw, expected):
    assert admin_api.normalize_quota_value(raw, "parse_limit") == expected


@pytest.mark.parametrize("raw", [-2, -999, 100_001, 1.5, "3", True, False, [0], {}])
def test_normalize_quota_value_rejects(raw):
    """纯函数级的拒绝。集成层已经验过 400，这里钉住判定本身。"""
    with pytest.raises(admin_api.QuotaValueError):
        admin_api.normalize_quota_value(raw, "parse_limit")


# ── 迁移：override 列幂等，老库起得来（工单 #12 的头号坑）─────
#
# `CREATE TABLE IF NOT EXISTS` 对已存在的表是空操作，所以只改建表语句的话
# 老库永远缺这两列，quota_limit 一读就报 no such column。断言直接查 PRAGMA，
# **不靠发请求触发**——本文件的 client 不跑 lifespan，没有任何 init_db
# 会在断言期间「顺手把护栏重建一遍」（工单 #7 的教训）。

def _user_columns(path) -> dict:
    with sqlite3.connect(str(path)) as conn:
        return {row[1]: row[3] for row in conn.execute("PRAGMA table_info(users)")}


def test_fresh_schema_has_nullable_override_columns(db):
    """NULL 的语义是「回落全局」，所以列必须**可空**、不能 NOT NULL DEFAULT 0。

    建成 NOT NULL DEFAULT 0 的话，每个新账号一出生就是「一天一条都不能用」。
    """
    columns = _user_columns(db.DB_PATH)
    for name in ("parse_limit_override", "chat_limit_override"):
        assert name in columns, f"新建库缺 {name}：{sorted(columns)}"
        assert columns[name] == 0, f"{name} 应可空，PRAGMA notnull={columns[name]}"


def test_new_users_default_to_null_overrides(db, make_user):
    uid = make_user(email="fresh@example.com")
    row = database.get_user_by_id(uid)
    assert row["parse_limit_override"] is None
    assert row["chat_limit_override"] is None


def test_legacy_users_table_gets_override_columns_and_is_idempotent(
        db, tmp_path, monkeypatch):
    from seams import close_all_thread_connections

    legacy = tmp_path / "legacy_users.db"
    with sqlite3.connect(str(legacy)) as conn:
        conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_vip INTEGER DEFAULT 0,
                vip_expire_at TEXT
            );
        """)
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@x.com','h')")

    monkeypatch.setattr(database, "DB_PATH", str(legacy))
    close_all_thread_connections()

    database.init_db()   # 老库补列
    database.init_db()   # 第二次必须不抛 duplicate column name

    names = [n for n in _user_columns(legacy)]
    assert names.count("parse_limit_override") == 1, names
    assert names.count("chat_limit_override") == 1, names
    with sqlite3.connect(str(legacy)) as conn:
        values = conn.execute(
            "SELECT parse_limit_override, chat_limit_override FROM users"
        ).fetchall()
    assert values == [(None, None)], (
        f"老行必须保持 NULL（回落全局），实得 {values}"
    )


# ── 凭据：响应体里一律不许出现（ADR 0004 / 0011）───────────

def test_no_endpoint_leaks_credential_shaped_fields(client_app, db, make_user):
    """真扫响应体的每一个键。

    只断言 200 是不够的：把 password_hash 加进响应不影响状态码，
    而那正是「白名单漏一列」时会发生的事。
    """
    client, _, plain_id = client_app
    target = make_user(email="target@example.com")
    database.reserve_video("https://v.example/x", plain_id)

    payloads = {
        "GET /api/models": client.get("/api/models").json(),
        "GET /api/admin/models": client.get("/api/admin/models", headers=_admin_hdr(client_app)).json(),
        "GET /api/admin/users": client.get("/api/admin/users", headers=_admin_hdr(client_app)).json(),
        "GET /api/admin/community": client.get(
            "/api/admin/community", headers=_admin_hdr(client_app)).json(),
        "POST quota": client.post(f"/api/admin/users/{target}/quota",
                                  json={"parse_limit": 1}, headers=_admin_hdr(client_app)).json(),
    }
    for where, payload in payloads.items():
        _assert_no_credentials(payload, where)


def test_admin_users_never_returns_password_hash(client_app):
    """users 表里确实有 password_hash——白名单漏了它就是发散列。"""
    rows = client_app[0].get("/api/admin/users", headers=_admin_hdr(client_app)).json()["items"]
    assert rows
    for row in rows:
        assert "password_hash" not in row
        assert set(row) == {
            "id", "email", "is_admin", "is_vip", "vip_expire_at", "created_at",
            "parse_used", "chat_used", "parse_limit", "chat_limit",
            "parse_limit_override", "chat_limit_override",
            "parse_limit_source", "chat_limit_source",
        }
