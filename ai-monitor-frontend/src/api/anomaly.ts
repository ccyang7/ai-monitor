import { http } from './client'
import type {
  AnomalyListOut,
  AnomalyParams,
  AnomalySettingsIn,
  AnomalySettingsOut,
} from '@/types/anomaly'

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

export async function fetchAnomalySettings(
  projectId: number,
): Promise<AnomalySettingsOut> {
  const { data } = await http.get<AnomalySettingsOut>(
    `/projects/${projectId}/anomaly-settings`,
  )
  return data
}

// 整体替换，不是「只改传来的字段」：三项必须一起发，null 表示这一项跟随默认。
// 想只调一项的话，另外两项要显式把当前值发回去，不然就被 null 清成默认了
export async function saveAnomalySettings(
  projectId: number,
  payload: AnomalySettingsIn,
): Promise<AnomalySettingsOut> {
  const { data } = await http.put<AnomalySettingsOut>(
    `/projects/${projectId}/anomaly-settings`,
    payload,
  )
  return data
}
