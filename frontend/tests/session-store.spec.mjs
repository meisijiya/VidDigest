/**
 * 会话 store：服务端说「这个 token 我不认」时，本地那份会话必须作废。
 *
 * ## 这个缺陷是怎么来的
 *
 * localStorage 里存着 `auth_token` + `auth_user`。**第一帧**按它渲染，
 * 是有意的（避免挂载时顶栏空一拍）——这一条不是缺陷。
 *
 * 缺陷在于：挂载后 App.vue 会回查一次 `/api/auth/me`（`session.refresh(false)`），
 * 服务端对过期/无效 token 回 **401**。而 `refresh` 的 catch 分支当时只写了
 * `isAdmin.value = false`：**种子里的 `user` 原封不动留着**。
 * 于是顶栏一直显示那个旧邮箱和「退出」，而同一时刻服务端把每个请求都判成
 * 未登录（额度面板会显示「登录后可用」）。用户看到的就是
 * 「明明没登录，界面却显示成登录态」。
 *
 * 权威在服务端（ADR 0010）：回查的结果是 401，就该把本地会话清掉，
 * 让界面回到未登录。localStorage 是**种子**，不是结论。
 *
 * ## 判据
 *
 * 断言的是 store 对外可见的状态与 localStorage 的落盘结果，
 * 不是内部实现：顶栏读的就是 `store.user`，落盘读的就是 localStorage。
 *
 * 每条都配反向对照：**只有 401** 才作废会话。网络故障与 5xx 是「问不着」，
 * 不是「被拒」——那时把人踢下线等于把一次抖动变成一次登出。
 */
import { describe, test, expect, vi, beforeEach } from 'vitest'
import { createPinia } from 'pinia'

// 只替换走网络的 `fetchMe`；`getSavedUser` / `isLoggedIn` / `logout` 保留真实现——
// 它们读写的正是 localStorage，而本文件要断言的就是 localStorage 的结果。
vi.mock('../src/api/auth.ts', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, fetchMe: vi.fn() }
})

import { useSessionStore } from '../src/stores/session.ts'
import { fetchMe } from '../src/api/auth.ts'

/** axios 的错误形状：状态码挂在 `response.status` 上。 */
function httpError(status) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status } })
}

const stale = { id: 99, email: 'stale@example.com', is_admin: false }

beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
})

describe('会话回查 · 服务端不认这个 token 时本地会话作废', () => {
  test('401：顶栏回到未登录态，localStorage 里的 token 与用户一起清掉', async () => {
    localStorage.setItem('auth_token', 'expired-token')
    localStorage.setItem('auth_user', JSON.stringify(stale))
    fetchMe.mockRejectedValue(httpError(401))

    const store = useSessionStore(createPinia())
    await store.refresh(false)

    expect(store.user, '服务端已判未登录，顶栏却还显示着旧邮箱').toBeNull()
    expect(store.token, '失效的 token 还留在内存里').toBeNull()
    expect(localStorage.getItem('auth_token'), '失效的 token 还留在 localStorage，下一次挂载还会再试一次').toBeNull()
    expect(localStorage.getItem('auth_user'), '失效的会话还留在 localStorage').toBeNull()
  })

  test('反向对照：500 是「问不着」不是「被拒」，会话必须留着', async () => {
    localStorage.setItem('auth_token', 'good-token')
    localStorage.setItem('auth_user', JSON.stringify(stale))
    fetchMe.mockRejectedValue(httpError(500))

    const store = useSessionStore(createPinia())
    await store.refresh(false)

    expect(store.user, '一次服务端故障就把人踢下线了').toEqual(stale)
    expect(localStorage.getItem('auth_token'), '一次服务端故障就把 token 丢了').toBe('good-token')
  })

  test('本地本来就没有 token：不发请求，也不碰 localStorage', async () => {
    fetchMe.mockRejectedValue(httpError(401))

    const store = useSessionStore(createPinia())
    await store.refresh(false)

    expect(fetchMe, '没登录还去回查，白白多一个 401').not.toHaveBeenCalled()
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(store.user).toBeNull()
  })
})
