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
