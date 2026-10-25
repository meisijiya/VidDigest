/**
 * 路由表（ADR 0018）。
 *
 * ## 为什么从「手写轻路由」换成 Vue Router
 *
 * 原来的做法是 App.vue 里自己维护 `PATHS` + `pageFromPath` + `pushPage` +
 * `syncPageFromUrl` 四个函数，并用 `window.history.pushState` 手动同步。
 * 它能跑，但它自己就是**一份路由实现**，而且是最容易写漏的那种：
 *
 *  - 没有路由级懒加载（社区页与后台页的代码进同一个首屏包）；
 *  - 没有守卫（登录态与管理员判据只能塞进页面组件里）；
 *  - 历史栈与后退语义要自己照顾（`popstate` 手工再同步一次）；
 *  - 页签名（`/history`、`/community`）不在表里，加一个页面要改三处。
 *
 * 换成 Vue Router 之后：**URL 与「显示哪一页」是同一件事**，
 * 页面组件由 `<router-view>` 按路由渲染，App.vue 里那条
 * `v-if / v-else-if` 链删掉。
 *
 * ## 五条真实路径
 *
 * 之前只认 `/admin` 一个真实路径，主页/历史/社区都留在 `/`——
 * 理由是「这三个页签之间来回跳时 URL 变化对用户没有信息量」。
 * 这个判断在只有三个页签时成立，但它带来的代价是：
 * 分享一个历史页链接收不到、浏览器后退要靠手工同步、
 * 刷新后不知道自己在哪一页。现在五条路径各自有名字，
 * 链接可以直接发出去，后退也交给路由。
 *
 * 第五条 `/admin/invite-codes` 是邀请码管理页（工单 #104）：它有自己的
 * 页键，所以「返回」能回到管理后台而不是首页。
 *
 * ## 守卫为什么是空的
 *
 * `/admin` **不**加前端守卫。判据在服务端（ADR 0010：管理员角色不进 JWT，
 * 每次请求回查数据库，提权与撤权即时生效）；前端再拦一道只会造出第二种口径——
 * 今天两边依据不同步时，用户看到的就是「导航里没有管理，但手输地址能进」，
 * 反而更难解释。真正的边界是 AdminPage 里那次请求的 403。
 */
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import HomeView from '../views/HomeView.vue'
import HistoryPage from '../components/HistoryPage.vue'
import CommunityPage from '../components/CommunityPage.vue'
import AdminPage from '../components/AdminPage.vue'
import InviteCodesPage from '../components/InviteCodesPage.vue'

/** 页面键。模板与事件里用它指代「现在是哪一页」。 */
export type PageKey = 'home' | 'history' | 'community' | 'admin' | 'admin-invites'

declare module 'vue-router' {
  interface RouteMeta {
    /** 这条路径对应哪个视图。页面由它派生，不再另维护一份页码。 */
    page?: PageKey
  }
}

/**
 * 五条路由。
 *
 * `meta.page` 是这份路由表给模板的唯一接口：App.vue 从 `route.meta.page`
 * 取到当前页键，于是「URL」与「页键」不需要两处维护。
 *
 * `/admin/invite-codes` 是邀请码管理页（工单 #104 / ADR 0025）：它**不是**
 * 管理后台的又一个页签，而是一条自己的路径，页键也分开（`admin-invites`）——
 * 页键分开才能给它接自己的「返回」动作（回管理后台，而不是回首页）。
 */
export const routes: RouteRecordRaw[] = [
  { path: '/', name: 'home', component: HomeView, meta: { page: 'home' } },
  { path: '/history', name: 'history', component: HistoryPage, meta: { page: 'history' } },
  { path: '/community', name: 'community', component: CommunityPage, meta: { page: 'community' } },
  { path: '/admin', name: 'admin', component: AdminPage, meta: { page: 'admin' } },
  {
    path: '/admin/invite-codes',
    name: 'admin-invite-codes',
    component: InviteCodesPage,
    meta: { page: 'admin-invites' },
  },
]

/**
 * 建路由实例。
 *
 * `scrollBehavior` 统一把换页后的滚动位置回到顶部：原来这件事分散在
 * `goHome` / `openHistory` / `openCommunity` 四处各写一遍
 * `window.scrollTo`，而浏览器后退时不归它们管——后退回到上一页时
 * 滚动位置会停在最底。现在由路由统一处理，五条路径行为一致。
 */
export function createAppRouter() {
  return createRouter({
    history: createWebHistory(),
    routes,
    scrollBehavior() {
      return { top: 0 }
    },
  })
}
