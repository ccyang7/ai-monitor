// 对应后端 app/schemas/stats.py

import type { AnomalyCounts } from './anomaly'

// 时间区间的共同约定：四个接口都收 start_date / end_date，都是左闭右开 [start, end)
export interface StatsParams {
  start_date: string
  end_date: string
  // 分桶粒度。由 buildTimeRange 按跨度决定：正好一个本地日历日时才是 hour。
  // 不传后端按 day —— 老调用方不受影响
  granularity?: 'day' | 'hour'
  // 分桶时区，IANA 名。不传后端按 UTC 切天，UTC+8 的用户会看到「今天」的数据被切一半到昨天
  tz?: string
}

export interface PeriodMetrics {
  calls: number
  total_tokens: number
  // 输入中命中缓存的部分（子集），用来解释成本涨跌
  cache_hit_tokens: number
  cost: number
  // 区间内一次成功调用都没有时是 null，不是 0 —— 后者会被读成「响应时间 0 毫秒」
  avg_latency: number | null
}

export interface OverviewOut {
  summary: PeriodMetrics
  // 上一个等长区间的同口径指标，环比由前端算（不是金额，不涉及精度口径）
  previous: PeriodMetrics
  // 按类型分的异常条数，喂统计卡角标和简报条。阶段 9 接上之前这个字段整个不存在，
  // 前端不去判断「功能做了没」—— 字段有了就说明能用
  anomaly_counts: AnomalyCounts
  // token / latency 这两类判没判。为 false 时 anomaly_counts 里的 0 不代表「正常」，
  // 只代表「样本还不够，还没开始判」—— 简报条和一个统计卡角标要靠它改措辞
  baseline_available: boolean
}

export interface TokenSummary {
  calls: number
  input_tokens: number
  // 命中数是 input 的子集，未命中 = input_tokens - cache_hit_tokens
  cache_hit_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface TokenPoint {
  // 按天是 'YYYY-MM-DD'，按小时是 'YYYY-MM-DDTHH:MM' —— 两种情况它都只是个标签，
  // 不是 ISO 时刻，不要去 new Date() 解析
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
  cache_hit_tokens: number
  cost: number
  avg_latency: number | null
  // 三个口径的占比都由服务端算好，各自四舍五入到 1 位，加起来可能是 99.9 或 100.1。
  // 前端不要自己拿 calls / cost 去算 —— 金额不做前端运算
  call_percentage: number
  token_percentage: number
  cost_percentage: number
}

export interface ModelsOut {
  items: ModelUsageItem[]
}
