import { http } from './client'
import type { AnomalyListOut, AnomalyParams } from '@/types/anomaly'

// 窗口按哪一天切要跟着浏览器走，理由同 stats.ts —— 但两个模块各存一份，
// 是为了让任何一个接口文件都能单独看懂自己发出去的参数
const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'

export async function fetchAnomalies(
  projectId: number,
  params: AnomalyParams,
): Promise<AnomalyListOut> {
  const { data } = await http.get<AnomalyListOut>(
    `/projects/${projectId}/anomalies`,
    { params: { tz: BROWSER_TZ, ...params } },
  )
  return data
}
