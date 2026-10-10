// 对应后端 app/schemas/user.py
export interface User {
  id: number
  username: string
  email: string | null
  created_at: string
}
