---
status: accepted
date: 2026-10-25
---

# ADR 0025：邀请码管理页是独立路由

## 决定

邀请码的管理界面是**独立路由** `/admin/invite-codes`，**不是**管理后台里的第四个页签，
也不是折叠区块。

- 页面组件：`frontend/src/components/InviteCodesPage.vue`（自己取数、自己管筛选与分页）。
- 路由：`frontend/src/router/index.ts` 里第五条路由，`meta.page` 是**独立页键**
  `admin-invites`（不是 `admin`）。页键分开才有自己的「返回」动作：它回**管理后台**，
  不是回首页。
- 入口：管理后台里留一个按钮，发 `open-invite-codes` 事件，由 `App.vue` 的
  `listenersFor` 接到路由跳转。组件里不直接用 router。
- 三个管理端点的能力在页面上是**全的**：批量发放（一次 1–20 枚）、按状态筛选
  （active / exhausted / expired / revoked）、分页、发放人回显、作废。
- **明文只在「刚发放的码」那一块出现**，列表里只有脱敏码（`code_masked`）。
- 筛选与显示用**同一份状态判定**：服务端把 `invite_code_status()` 注册成 SQLite UDF，
  列表的 `WHERE` 子句与返回项的 `status` 字段调的是同一个函数——两处各判一次就会
  出现「筛出来是可用、点进去却是已过期」。

## 为什么

- **页签是「同一批数据的不同视图」，邀请码是另一件事**。管理后台的三个页签
  （用户 / 社区 / 模型）切的是同一批实体的不同侧面；邀请码有自己的生命周期
  （发、用完、过期、作废）、自己的表格密度（码、状态、用量、过期、备注、发放人）、
  自己的操作节奏（批量发码、筛选、分页）。塞进页签的结果是后台页变长、
  每个页签都要为另一个页面的内容让出空间。
- **折叠区块的代价是「进后台就替管理员打一次接口」**：区块折叠着时列表不加载，
  展开才加载——这是对的；但它把入口藏在后台页深处，管理员要找两跳。
  独立路由可以直接把链接发给同事。
- **页键必须分开**：如果新页复用 `admin` 页键，`listenersFor('admin')` 只能给一个
  `back`，而这一页要回的是管理后台、不是首页。两页键才能各接各的返回。
- **筛选与显示必须同源**：如果 `WHERE` 用 SQL 表达式而返回项用 Python 函数算，
  两份逻辑会在「快过期」这类边界上分叉（一个是 active、一个是 expired）。
  注册成 UDF 之后，筛选和投影天然是同一个函数。
- **批量发放要设上限**：一次最多 20 枚。批量接口没有上限时，管理员手滑一次就是
  200 枚码——明文一次性返回 200 条，人是拿不住的，那批码多半就废了。

## 代价

- **多一条路由、多一个页键**：顶栏的高亮判据要跟着改（`admin` 与 `admin-invites`
  都要高亮「管理」），路由表的冻结断言也变了。
- **管理后台里不再有任何邀请码界面**，只剩一个入口按钮。有人找不到入口的概率上升，
  靠入口按钮上的「管理邀请码」六个字降低。
- **前端 api 层多三个出口**，而 `frontend/src/api/admin.ts` 的导出名单被
  `frontend/tests/admin-ui.test.mjs` 冻结；组件只许从 api 层取数据、自己不许翻
  snake_case（转换只该有一份）。
- **状态判定注册成 UDF**：SQLite 里多了一个自定义函数，`list_invite_codes` 的
  `WHERE` 因此不能走纯 SQL 索引（量小，无所谓；量大了要另说）。
- **前端多了一条「返回」链路**（后台 → 邀请码页 → 后台），比折叠区块多一次跳转。

## 推翻条件

- 要把邀请码也做成管理后台的一个页签（那要改 ADR 0023，并把这一页的
  取数、分页、筛选重新并进后台页的页签体系）。
- 要给管理后台做统一的左侧导航（那时独立路由与导航项会重复，那时应该做导航，
  而不是继续往路由表里加第五条路径）。
- 要一个码绑定邮箱或设备（那是注册语义的变化，见
  [ADR 0023](0023-invite-code-registration.md) 的推翻条件）。

## 后果

- 页面组件在 `frontend/src/components/InviteCodesPage.vue`；
  路由与页键在 `frontend/src/router/index.ts`；接线在 `frontend/src/App.vue`
  的 `listenersFor`；api 出口在 `frontend/src/api/admin.ts`。
- 数据层在 `backend/database.py`：`invite_code_status()`（UDF）、
  `create_invite_codes()`、`list_invite_codes(limit, offset, status)`、
  `INVITE_CODE_BATCH_MAX = 20`。端点在 `backend/admin_api.py`。
- 管理端点清单与语义写在 `docs/API.md`；运维口径在 `docs/OPERATIONS.md` §10.6；
  面向管理员的操作说明在 `docs/INVITE-CODES.md`。
- **本 ADR 修正了 [ADR 0023](0023-invite-code-registration.md) 里「折叠的独立区块、
  不是第四个页签」那一条**——那一条描述的是当时的界面形态，现在改成独立路由。
  ADR 0023 关于注册语义（码怎么发、怎么消费、怎么失效）仍然有效。
- 真挂载判据在 `frontend/tests/invite-codes-page.mount.spec.mjs`；
  静态接线判据在 `frontend/tests/invite-codes-page.test.mjs`；
  两条变异（M30 列表改成显示完整码、M31 打开页面时不加载列表）在
  `frontend/tests/mutation-ui-fixes.mjs`。
