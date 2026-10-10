import { http } from './client'
import type { LogDetail, LogListOut, LogListParams } from '@/types/log'

// 值为 undefined 的条件 axios 不会拼进查询串，所以调用方只要「没填就不设」，
// 不用自己过滤空条件
export async function fetchLogs(
  projectId: number,
  params: LogListParams,
): Promise<LogListOut> {
  const { data } = await http.get<LogListOut>(`/projects/${projectId}/logs`, { params })
  return data
}

export async function fetchLogDetail(logId: number): Promise<LogDetail> {
  const { data } = await http.get<LogDetail>(`/logs/${logId}`)
  return data
}
