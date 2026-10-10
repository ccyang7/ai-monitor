// 对应后端 app/schemas/auth.py
export interface RegisterIn {
  username: string
  password: string
  email?: string | null
}

export interface LoginIn {
  username: string
  password: string
}

export interface TokenOut {
  access_token: string
  token_type: string
  expires_in: number
}
