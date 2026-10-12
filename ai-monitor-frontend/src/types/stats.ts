// 对应后端 app/schemas/stats.py

// 时间区间的共同约定：四个接口都收 start_date / end_date，都是左闭右开 [start, end)
export interface StatsParams {
  start_date: string
  end_date: string
  // 分桶时区，IANA 名。不传后端按 UTC 切天，UTC+8 的用户会看到「今天」的数据被切一半到昨天
  tz?: string
}

export interface PeriodMetrics {
  calls: number
  total_tokens: number
  cost: number
  // 区间内一次成功调用都没有时是 null，不是 0 —— 后者会被读成「响应时间 0 毫秒」
  avg_latency: number | null
}

export interface RecentLogItem {
  id: number
  model: string
  total_tokens: number
  latency: number
  status: 'success' | 'error'
  created_at: string
}

export interface OverviewOut {
  summary: PeriodMetrics
  // 上一个等长区间的同口径指标，环比由前端算（不是金额，不涉及精度口径）
  previous: PeriodMetrics
  // 不用时间选择器过滤：这是项目最新发生的调用，选了近 30 天也不该看不见今天刚上报的
  recent_logs: RecentLogItem[]
  // 阶段 9 接上之前恒为 null —— 前端据此区分「没异常」和「异常功能还没做」
  anomaly_count: number | null
}

export interface TokenSummary {
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface TokenPoint {
  // 'YYYY-MM-DD'，不是 ISO 时刻 —— 它就是个日期标签，不要去 new Date() 解析
  date: string
  calls: number
  total_tokens: number
}

export interface TokensOut {
  summary: TokenSummary
  series: TokenPoint[]
}

export interface CostSummary {
  calls: number
  cost: number
}

export interface CostPoint {
  date: string
  calls: number
  cost: number
}

export interface CostsOut {
  summary: CostSummary
  series: CostPoint[]
}

export interface ModelUsageItem {
  model: string
  calls: number
  total_tokens: number
  cost: number
  avg_latency: number | null
  // 服务端算好的，按调用次数占比，各项加起来可能是 99.9 或 100.1（显示口径）
  percentage: number
}

export interface ModelsOut {
  items: ModelUsageItem[]
}
