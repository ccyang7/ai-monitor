// 对应后端 app/schemas/api_key.py。不含 key_hash —— 后端根本不返回这个字段

export interface ApiKey {
  id: number
  key_prefix: string
  status: string
  created_at: string
  expired_at: string | null
}

export interface ApiKeyCreated extends ApiKey {
  api_key: string
}
