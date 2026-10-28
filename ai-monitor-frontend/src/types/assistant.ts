// 对应后端 app/schemas/admin.py 的助手用量模型
export interface AdminAssistantSummary {
  total_chats: number
  total_tokens: number
  error_count: number
  active_users: number
}

// 只有数字与账号名：后端表里不存提问和回答的正文，这里也没有可显示的对话内容
export interface AdminAssistantUserRow {
  user_id: number
  username: string
  chat_count: number
  total_tokens: number
  tool_call_count: number
  avg_latency: number
  error_count: number
  last_used_at: string
}

export interface AdminAssistantOut {
  items: AdminAssistantUserRow[]
  total: number
  page: number
  page_size: number
  summary: AdminAssistantSummary
}
