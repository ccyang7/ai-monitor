// 对应后端 app/schemas/anomaly.py

import type { StatsParams } from './stats'

// 四种类型共用一条记录结构，各自只填自己有的字段（见后端 AnomalyItem）
export type AnomalyType = 'token' | 'latency' | 'error' | 'error_rate'

// 去掉 granularity：异常列表不做分桶，这个接口根本不收这个参数。留着的话
// buildTimeRange 的返回值整个展开时会把一个没人认的参数发出去，白占带宽也脏了日志
export interface AnomalyParams extends Omit<StatsParams, 'granularity'> {
  type?: AnomalyType
  page?: number
  page_size?: number
}

export interface AnomalyItem {
  type: AnomalyType
  // 窗口类异常给的是窗口起点的时刻，不是「异常发生的时刻」
  created_at: string

  // 单条判定（token / latency / error）有 log_id，窗口判定（error_rate）没有；
  // 反过来只有窗口判定有 window。前端据此决定这一行是「跳日志详情」还是「只读」
  log_id: number | null
  window: string | null

  model: string | null

  // 窗口判定的 baseline 装的是阈值（0.2）而不是滑动基线 —— 让四种类型能用同一套
  // 「实际值 vs 参考值」渲染，不必为错误率单写一条分支
  value: number | null
  baseline: number | null
  ratio: number | null

  // 只有失败调用有
  error_message: string | null
}

export interface AnomalyCounts {
  token: number
  latency: number
  error: number
  error_rate: number
}

export interface InsufficientBaseline {
  model: string
  samples: number
  required: number
}

export interface AnomalyListOut {
  items: AnomalyItem[]
  total: number
  page: number
  page_size: number

  // 「没有异常」和「样本不足、还没法判定」是两回事：false 时列表未必是空的 ——
  // 失败调用和错误率不依赖基线，项目刚接入也能报出来
  baseline_available: boolean

  // 只列样本不足的模型，达标的不在里面
  insufficient_baselines: InsufficientBaseline[]
}
