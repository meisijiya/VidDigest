---
status: accepted
date: 2026-10-23
---

# 前端路由：轻量自实现，不引 vue-router

**决定**：前端不引入 `vue-router`。URL 与 `currentPage` 的双向同步由一段自实现代码
承担（`frontend/src/App.vue` 的 `PATHS` / `pageFromPath` / `pushPage` /
`syncPageFromUrl`），只认 `/admin` 一个真实路径，主页 / 历史 / 社区三种视图共享 `/`。

## 为什么

**为什么不是 `vue-router`**：本项目的前端只有四个视图，其中三个（主页、历史、社区）
在产品语义上就是同一个页面的不同页签——它们之间来回跳时，URL 变化对用户没有信息量。
引入 `vue-router` 要付出的代价是：为了一处路由引入一整套依赖与生命周期模型，
再为「三个页签共用一个路径」去写 `redirect` / `alias` / `beforeEnter` 之类的配置绕开它。

代价是明确的：**这份手写路由没有覆盖 `vue-router` 的能力边界**——
没有嵌套路由、没有路由守卫、没有异步懒加载、没有导航钩子。
如果前端将来长出真正的多层级页面（例如社区条目详情页），
或者需要按角色决定可见路由，这个决定就该被推翻，改为引 `vue-router`。
在那之前，**更小的依赖面与更少的抽象层**是更划算的选择。

**为什么不用 hash 路由**：hash 会把 `#/admin` 交给前端、后端都看不见；
而 `/admin` 需要服务端回落到 `index.html`（Nginx 同域托管 + `try_files ... /index.html`，
见 `docs/OPERATIONS.md`）。

## Consequences

- 前端 `package.json` 里没有 `vue-router`，这是**决定的结果**而不是遗漏。
  `frontend/src/App.vue` 里引用本 ADR 的注释是这条决定的落点。
- 管理员入口的判定与鉴权跟路由无关：ADR 0010 记的是「管理员角色不进令牌载荷、
  每次请求回查数据库」，真正的边界是 `AdminPage` 里那次请求的 403。
  **不要把本 ADR 当成 ADR 0010 的引用点。**
- `scripts/check_refs.py` 会在源码注释里的 `ADR NNNN` 指向不存在的 ADR 文件时转红。
  本 ADR 存在的另一半理由，就是修掉 `frontend/src/App.vue` 里把「不引 vue-router」
  错记成 ADR 0010 的错位——那段注释说的是本决定，ADR 0010 记的是管理后台范围，
  两者都还在，却互相对不上。
