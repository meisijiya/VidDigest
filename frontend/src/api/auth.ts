import axios from 'axios'

const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

/**
 * 登录用户。
 *
 * 字段是**开放**的：登录响应与 /api/auth/me 给回来的字段并不一致
 * （注册响应里就没有 is_admin，见 backend/api_auth.py），多出来的字段
 * 要能原样带过去，所以这里带索引签名而不是把字段逐个列死。
 */
export interface SessionUser {
  id?: number
  email?: string
  is_admin?: boolean
  is_vip?: boolean
  [key: string]: unknown
}

axios.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * token 的读取出口。
 *
 * 落盘只有这一处（localStorage）。别的地方要 token 一律调它，
 * 免得出现第二个真相源。
 */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function isLoggedIn(): boolean {
  return !!getToken()
}

export function getSavedUser(): SessionUser | null {
  const data = localStorage.getItem(USER_KEY)
  return data ? (JSON.parse(data) as SessionUser) : null
}

export function saveAuth(token: string, user: SessionUser): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export async function register(email: string, password: string): Promise<SessionUser> {
  const res = await axios.post('/api/auth/register', { email, password })
  const { token, user } = res.data.data
  saveAuth(token, user)
  return user
}

export async function login(email: string, password: string): Promise<SessionUser> {
  const res = await axios.post('/api/auth/login', { email, password })
  const { token, user } = res.data.data
  saveAuth(token, user)
  return user
}

export async function fetchMe(): Promise<SessionUser> {
  const res = await axios.get('/api/auth/me')
  const user = res.data.data as SessionUser
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  return user
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
