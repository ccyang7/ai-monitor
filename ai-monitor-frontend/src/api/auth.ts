import { http } from './client'
import type { LoginIn, RegisterIn, TokenOut } from '@/types/auth'
import type { User } from '@/types/user'

export async function register(payload: RegisterIn): Promise<User> {
  const { data } = await http.post<User>('/auth/register', payload)
  return data
}

export async function login(payload: LoginIn): Promise<TokenOut> {
  const { data } = await http.post<TokenOut>('/auth/login', payload)
  return data
}
