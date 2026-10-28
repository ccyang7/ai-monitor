import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as authApi from '@/api/auth'
import { tokenStorage } from '@/api/client'
import { fetchMe } from '@/api/users'
import type { LoginIn, RegisterIn } from '@/types/auth'
import type { User } from '@/types/user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(tokenStorage.read())
  const user = ref<User | null>(null)
  const restored = ref(false)

  const isLoggedIn = computed(() => token.value !== null)
  // 只是菜单开关：绕过它直接调 /admin 接口，后端照样返回 403
  const isAdmin = computed(() => user.value?.role === 'admin')

  function setToken(value: string): void {
    token.value = value
    tokenStorage.write(value)
  }

  function clear(): void {
    token.value = null
    user.value = null
    tokenStorage.clear()
  }

  async function loadUser(): Promise<void> {
    user.value = await fetchMe()
  }

  async function login(payload: LoginIn): Promise<void> {
    const { access_token } = await authApi.login(payload)
    setToken(access_token)
    await loadUser()
  }

  async function register(payload: RegisterIn): Promise<void> {
    await authApi.register(payload)
    await login({ username: payload.username, password: payload.password })
  }

  // localStorage 里的 token 可能已经过期或被吊销，它本身说明不了任何事。
  // 必须请求一次 /users/me 才能确定身份，否则会带着废 token 渲染出一个空白的已登录界面
  async function restore(): Promise<void> {
    if (restored.value) return
    if (!token.value) {
      restored.value = true
      return
    }
    try {
      await loadUser()
    } catch {
      clear()
    } finally {
      restored.value = true
    }
  }

  return {
    token,
    user,
    restored,
    isLoggedIn,
    isAdmin,
    login,
    register,
    loadUser,
    clear,
    restore,
  }
})
