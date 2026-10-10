import type { ProjectListItem } from '@/types/project'

export type ProjectHealth = 'unknown' | 'ok' | 'alert'

// 一律从服务端字段推导，不在前端猜。last_called_at 为 null 说明项目还没上报过数据，
// 这和「上报过且正常」是两回事，不能都显示成绿点
export function projectHealth(project: ProjectListItem): ProjectHealth {
  if (project.has_anomaly) return 'alert'
  if (!project.last_called_at) return 'unknown'
  return 'ok'
}

export const HEALTH_LABEL: Record<ProjectHealth, string> = {
  unknown: '未接入数据',
  ok: '正常',
  alert: '有异常',
}

export const HEALTH_COLOR: Record<ProjectHealth, string> = {
  unknown: '#9AA9A2',
  ok: '#52B788',
  alert: '#D98E2B',
}

export const TECH_STACK_OPTIONS = ['Python', 'Java', 'Node.js', '其他']
