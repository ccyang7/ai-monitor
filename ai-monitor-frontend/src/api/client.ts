import axios from 'axios'
import type { AxiosError } from 'axios'

const TOKEN_KEY = 'ai-monitor:token'

export const tokenStorage = {
  read: (): string | null => localStorage.getItem(TOKEN_KEY),
  write: (token: string): void => localStorage.setItem(TOKEN_KEY, token),
  clear: (): void => localStorage.removeItem(TOKEN_KEY),
}

// baseURL 用相对路径，开发时的跨域由 vite.config.ts 的 proxy 处理
export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 15000,
})

http.interceptors.request.use((config) => {
  const token = tokenStorage.read()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 没有这两个例外，用户输错密码会被弹回登录页，刚拿到的错误提示跟着被冲掉
const AUTH_ENDPOINTS = ['/auth/login', '/auth/register']

function isAuthEndpoint(url: string | undefined): boolean {
  return url !== undefined && AUTH_ENDPOINTS.some((path) => url.includes(path))
}

type UnauthorizedHandler = () => void
let unauthorizedHandler: UnauthorizedHandler | null = null

// 用回调而不是在这里 import router：router 依赖视图、视图依赖 api，直接引用会成环
export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401 && !isAuthEndpoint(error.config?.url)) {
      tokenStorage.clear()
      unauthorizedHandler?.()
    }
    return Promise.reject(error)
  },
)

const FIELD_LABELS: Record<string, string> = {
  username: '用户名',
  password: '密码',
  email: '邮箱',
  name: '项目名称',
  description: '项目描述',
  tech_stack: '技术栈',
}

interface ValidationDetail {
  loc: (string | number)[]
  msg: string
}

export function extractErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return '发生未知错误'
  }
  if (!error.response) {
    return error.code === 'ECONNABORTED'
      ? '请求超时，请检查后端服务是否正常'
      : '网络异常，请检查后端服务是否已启动'
  }

  const detail = (error.response.data as { detail?: unknown } | undefined)?.detail

  if (typeof detail === 'string') {
    return detail
  }

  if (Array.isArray(detail)) {
    // FastAPI 的 422 把 detail 塞成数组，直接渲染会显示成 [object Object]
    const first = detail[0] as ValidationDetail | undefined
    if (first) {
      const field = first.loc[first.loc.length - 1]
      const label = typeof field === 'string' ? (FIELD_LABELS[field] ?? field) : ''
      return label ? `${label}：${first.msg}` : first.msg
    }
  }

  return `请求失败（HTTP ${error.response.status}）`
}
