// 与后端 app/ai/stream.py 的 SSE 协议一一对应，改协议时两边一起改
export type ChatEvent =
  | { type: 'status'; stage: 'tool_start'; name: string; label: string }
  | { type: 'status'; stage: 'tool_end'; name: string }
  | { type: 'delta'; text: string }
  | { type: 'done'; thread_id: string }
  | { type: 'error'; message: string }

export type MessageRole = 'user' | 'assistant'

// aborted 要和 error 分开：用户自己按的停止，不该显示成红色的故障
export type MessageStatus = 'streaming' | 'done' | 'error' | 'aborted'

export interface ToolStatus {
  name: string
  label: string
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  status: MessageStatus
  tool: ToolStatus | null
  error: string | null
}

export interface ChatRequest {
  message: string
  thread_id: string | null
  current_project: string | null
}
