// 对应后端 app/schemas/model.py

export interface Model {
  id: number
  provider: string
  model_name: string
  // 三档价格，单位统一是「元 / 百万 Token」
  input_price: number
  output_price: number
  // null = 没有缓存档（命中的 Token 按输入价算），与「缓存价是 0」是两回事
  cache_hit_price: number | null
  is_builtin: boolean
  status: string
  // 服务端按「上报记录里出现过这个模型名」聚合出来的项目名
  used_by_projects: string[]
}

// 上报过、但在平台里找不到可用价格的模型名：页面据此提示用户去补价
export interface UnpricedModel {
  model_name: string
  // 上报数据里出现过这个名字的项目
  used_by_projects: string[]
}

// 管理后台：全平台「有人上报、但没有内置价」的模型
export interface AdminUnpricedModel {
  model_name: string
  call_count: number
  user_count: number
}

export interface ModelListOut {
  items: Model[]
  total: number
  unpriced_models: UnpricedModel[]
}

export interface ModelCreateIn {
  provider: string
  model_name: string
  input_price: number
  output_price: number
  cache_hit_price?: number | null
}

// 只发真正改过的字段：后端按「没传就不动」处理；
// cache_hit_price 传 null 是「清空缓存档」，不是「别动它」
export interface ModelUpdateIn {
  provider?: string
  model_name?: string
  input_price?: number
  output_price?: number
  cache_hit_price?: number | null
}
