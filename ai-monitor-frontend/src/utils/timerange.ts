// 时间范围的统一换算：把用户在日历上选的「哪几天」变成绝对时刻区间 [start, end)。
//
// 这一步只能在前端做 —— 服务端不知道用户在哪个时区，「今天 0 点」是个歧义句。
// 日志页和概览页共用这一份，两边的「近 7 天」必须是同一个区间。

export type RangePreset = 'today' | '7d' | '30d' | 'custom' | ''

export interface TimeRangeValue {
  preset: RangePreset
  customStart: string
  customEnd: string
}

export interface TimeRangeQuery {
  start_date: string
  end_date: string
}

export const DEFAULT_RANGE: TimeRangeValue = {
  preset: '7d',
  customStart: '',
  customEnd: '',
}

export const ALL_TIME: TimeRangeValue = {
  preset: '',
  customStart: '',
  customEnd: '',
}

export const PRESET_OPTIONS: { value: RangePreset; label: string }[] = [
  { value: 'today', label: '今日' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: 'custom', label: '自定义范围' },
]

function startOfLocalDay(date: Date): Date {
  const start = new Date(date)
  start.setHours(0, 0, 0, 0)
  return start
}

// 用 setDate 而不是加毫秒数：跨月、跨年（还有夏令时切换，如果哪天要支持别的时区）
// 的进位让 Date 自己算，加 86400000 在夏令时那天会偏一小时
function addDays(date: Date, days: number): Date {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

// 只写 '2026-10-01' 的话浏览器按 UTC 零点解析，必须补上时间部分才是本地时间
function parseInputDate(value: string): Date {
  return new Date(`${value}T00:00:00`)
}

/**
 * 区间是左闭右开：右端取「用户选的最后一天的次日 0 点」，而不是当天 23:59:59.999。
 * 后端一律用 `< end`，这样正好含满最后一天，也不会漏掉 23:59:59.999 之后到午夜之间的记录。
 *
 * 返回 null 表示「不限时间」—— 只有 preset 为空、或自定义范围还没填全时才会出现。
 * 概览页要求必须有区间，调用方自己决定遇到 null 怎么办。
 */
export function buildTimeRange(value: TimeRangeValue): TimeRangeQuery | null {
  const today = startOfLocalDay(new Date())
  const tomorrow = addDays(today, 1)

  switch (value.preset) {
    case 'today':
      return { start_date: today.toISOString(), end_date: tomorrow.toISOString() }

    case '7d':
      return {
        start_date: addDays(today, -6).toISOString(),
        end_date: tomorrow.toISOString(),
      }

    case '30d':
      return {
        start_date: addDays(today, -29).toISOString(),
        end_date: tomorrow.toISOString(),
      }

    case 'custom': {
      if (!value.customStart || !value.customEnd) return null
      return {
        start_date: parseInputDate(value.customStart).toISOString(),
        end_date: addDays(parseInputDate(value.customEnd), 1).toISOString(),
      }
    }

    default:
      return null
  }
}

/**
 * 两个日期填反了是个无效范围，发出去后端会回 422，而 422 的报错对用户没有意义。
 * 只在自定义范围且两端都填了时才判 —— 'YYYY-MM-DD' 的字典序就是时间序，直接比字符串。
 */
export function rangeError(value: TimeRangeValue): string {
  if (value.preset !== 'custom') return ''
  if (!value.customStart || !value.customEnd) return ''
  return value.customStart > value.customEnd ? '结束日期不能早于开始日期' : ''
}

export function rangeLabel(value: TimeRangeValue): string {
  const preset = PRESET_OPTIONS.find((option) => option.value === value.preset)
  if (!preset) return '全部时间'
  if (value.preset !== 'custom') return preset.label
  if (!value.customStart || !value.customEnd) return preset.label
  return `${value.customStart} 至 ${value.customEnd}`
}
