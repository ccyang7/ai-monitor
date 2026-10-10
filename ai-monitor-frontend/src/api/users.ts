import { http } from './client'
import type { User } from '@/types/user'

export async function fetchMe(): Promise<User> {
  const { data } = await http.get<User>('/users/me')
  return data
}
