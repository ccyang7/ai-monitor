function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function toDate(iso: string): Date | null {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDate(iso: string): string {
  const date = toDate(iso)
  if (!date) return '—'
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function formatDateTime(iso: string): string {
  const date = toDate(iso)
  if (!date) return '—'
  return `${formatDate(iso)} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function formatDateTimeFull(iso: string): string {
  const date = toDate(iso)
  if (!date) return '—'
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  return `${formatDate(iso)} ${time}`
}

export function formatNumber(value: number): string {
  return value.toLocaleString('zh-CN')
}

// 大数字的短写法，只给图表轴标签用：11,391,234 挤在轴上会把网格吃掉一半。
// 用「万 / 亿」而不是 k / M —— 中文语境下 15.5万 比 155k 好读。
// 它会丢精度（1139.1万 抹掉了后面 1234），所以指标卡和 tooltip 这类要读数的
// 地方一律用 formatNumber
export function formatCompact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}亿`
  if (abs >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return formatNumber(value)
}

// 毫秒和秒混在一张表里读起来很乱，统一成「不足 1 秒看毫秒，超过就看秒」
export function formatLatency(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

// 只做展示格式化，不参与任何运算 —— 金额口径以服务端返回的 estimated_cost 为准
export function formatCost(value: number | null): string {
  if (value === null) return '—'
  const amount = value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })
  return `¥${amount}`
}

export function formatRelative(iso: string | null): string {
  if (!iso) return '尚未接入数据'
  const date = toDate(iso)
  if (!date) return '—'

  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 86400 * 30) return `${Math.floor(seconds / 86400)} 天前`
  return formatDate(iso)
}

export function initials(username: string): string {
  return username.trim().charAt(0).toUpperCase() || '?'
}
