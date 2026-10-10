// 对应后端 app/schemas/log.py

export interface LogListItem {
  id: number
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  // 耗时，单位毫秒 —— 后端存的也是毫秒，换算成秒只发生在展示时
  latency: number
  status: 'success' | 'error'
  // 服务端算好的展示口径；为 null 表示这次调用没有成本可算
  estimated_cost: number | null
  created_at: string
}

export interface LogListOut {
  items: LogListItem[]
  total: number
  page: number
  page_size: number
}

// 列表刻意不带 input / output：大文本进列表会让一页响应膨胀到几 MB，只有详情才取
export interface LogDetail extends LogListItem {
  project_id: number
  provider: string
  input: string | null
  output: string | null
  error_message: string | null
}

// 对应后端 LogListQuery：字段全部可选，不传就是不过滤，彼此之间是 AND
export interface LogListParams {
  page?: number
  page_size?: number
  model?: string
  status?: 'success' | 'error'
  start_date?: string
  end_date?: string
  min_tokens?: number
  max_tokens?: number
  q?: string
}
