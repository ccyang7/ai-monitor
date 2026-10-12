import { http } from './client'
import type {
  CostsOut,
  ModelsOut,
  OverviewOut,
  StatsParams,
  TokensOut,
} from '@/types/stats'

// 分桶时区永远跟着浏览器走：这是唯一知道用户「今天」从几点开始的地方。
// 个别环境拿不到时区名，退回 UTC —— 空值发给后端是 422，不如给个合法值
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

// 统一注入，页面调用方不用管时区，也就不会有哪个页面忘了传
function withTimezone(params: StatsParams): StatsParams {
  return { tz: BROWSER_TZ, ...params }
}

export async function fetchOverview(
  projectId: number,
  params: StatsParams,
): Promise<OverviewOut> {
  const { data } = await http.get<OverviewOut>(
    `/projects/${projectId}/stats/overview`,
    { params: withTimezone(params) },
  )
  return data
}

export async function fetchTokenStats(
  projectId: number,
  params: StatsParams,
): Promise<TokensOut> {
  const { data } = await http.get<TokensOut>(`/projects/${projectId}/stats/tokens`, {
    params: withTimezone(params),
  })
  return data
}

export async function fetchCostStats(
  projectId: number,
  params: StatsParams,
): Promise<CostsOut> {
  const { data } = await http.get<CostsOut>(`/projects/${projectId}/stats/costs`, {
    params: withTimezone(params),
  })
  return data
}

export async function fetchModelStats(
  projectId: number,
  params: StatsParams,
): Promise<ModelsOut> {
  const { data } = await http.get<ModelsOut>(`/projects/${projectId}/stats/models`, {
    params: withTimezone(params),
  })
  return data
}
