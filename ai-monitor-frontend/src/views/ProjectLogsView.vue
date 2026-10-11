<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchLogDetail, fetchLogs } from '@/api/logs'
import AppDrawer from '@/components/AppDrawer.vue'
import JsonView from '@/components/JsonView.vue'
import ProjectTabs from '@/components/ProjectTabs.vue'
import { useProjectsStore } from '@/stores/projects'
import {
  formatCost,
  formatDateTime,
  formatDateTimeFull,
  formatLatency,
  formatNumber,
} from '@/utils/format'
import { tryParseJson } from '@/utils/json'
import { HEALTH_LABEL, projectHealth } from '@/utils/project'
import type { LogDetail, LogListItem, LogListParams } from '@/types/log'

const props = defineProps<{ projectId: string }>()

const projects = useProjectsStore()

const projectId = computed(() => Number(props.projectId))
// 页头要的项目名、技术栈已经在侧边栏那份列表里了，不必再单独拉一次
const project = computed(
  () => projects.items.find((item) => item.id === projectId.value) ?? null,
)

const PAGE_SIZE = 20
const DAY_MS = 24 * 60 * 60 * 1000

const TOKEN_RANGES: Record<string, { min?: number; max?: number }> = {
  lt1k: { max: 999 },
  '1k-10k': { min: 1000, max: 10000 },
  gt10k: { min: 10001 },
}

const filters = reactive({
  model: '',
  status: '' as '' | 'success' | 'error',
  range: '' as '' | 'today' | '7d' | '30d' | 'custom',
  customStart: '',
  customEnd: '',
  tokens: '' as '' | 'lt1k' | '1k-10k' | 'gt10k',
})

// 搜索词单独拿着而不是并进 filters：它是逐字变化的，和其他条件不是同一种节奏
const keyword = ref('')

const items = ref<LogListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const loadError = ref('')

// 后端还没有「这个项目用过哪些模型」的接口，下拉选项只能从查到的数据里攒。
// 攒而不是只看当前这页：翻页、改筛选条件时选项不该跟着变少
const knownModels = ref<string[]>([])

const selectedId = ref<number | null>(null)
const detail = ref<LogDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

// 接入方经常把原始 JSON 直接上报（messages 数组、模型返回的结构化结果），
// 探测出来就结构化渲染，探测不出（纯文本）保持原来的换行文本显示
const inputJson = computed(() => tryParseJson(detail.value?.input))
const outputJson = computed(() => tryParseJson(detail.value?.output))

const copied = ref<'' | 'input' | 'output'>('')
let copiedTimer: number | undefined

// 复制的是原文而不是渲染后的内容：结构化视图会折叠长文本、吃掉引号，
// 用户要粘到别处去比对时，那一份得跟数据库里的一模一样
async function copyRaw(which: 'input' | 'output', text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    // 从别的机器用 http://192.168.x.x 访问时不是安全上下文，clipboard API 直接不存在，
    // 退回已经被废弃但哪儿都能用的 execCommand
    const scratch = document.createElement('textarea')
    scratch.value = text
    scratch.style.position = 'fixed'
    scratch.style.opacity = '0'
    document.body.appendChild(scratch)
    scratch.select()
    document.execCommand('copy')
    document.body.removeChild(scratch)
  }
  copied.value = which
  window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(() => (copied.value = ''), 1600)
}

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const hasFilter = computed(() =>
  Boolean(
    filters.model ||
      filters.status ||
      filters.range ||
      filters.tokens ||
      keyword.value.trim(),
  ),
)

// 两个日期填反了就是个无效范围，发出去后端会回 422，而 422 的报错对用户没有意义，这里先拦下来
const rangeError = computed(() =>
  filters.range === 'custom' &&
  filters.customStart &&
  filters.customEnd &&
  filters.customStart > filters.customEnd
    ? '结束日期不能早于开始日期'
    : '',
)

const visiblePages = computed<(number | '…')[]>(() => {
  const last = pageCount.value
  const current = page.value
  if (last <= 7) return Array.from({ length: last }, (_, index) => index + 1)

  const windowStart = Math.max(2, current - 1)
  const windowEnd = Math.min(last - 1, current + 1)
  const pages: (number | '…')[] = [1]
  if (windowStart > 2) pages.push('…')
  for (let value = windowStart; value <= windowEnd; value += 1) pages.push(value)
  if (windowEnd < last - 1) pages.push('…')
  pages.push(last)
  return pages
})

// 只写 "2026-10-01" 的话浏览器会按 UTC 零点解析，必须补上时间部分才是本地时间
function parseLocalDate(value: string): Date {
  return new Date(`${value}T00:00:00`)
}

// 后端把「不带时区的时间」当 UTC，所以本地时间要先转成带时区的 ISO 再发，
// 直接发明文的话，「今天 00:00」会被当成 UTC 的今天 00:00，整体偏 8 小时
function localDayStart(date: Date): string {
  const start = new Date(date)
  start.setHours(0, 0, 0, 0)
  return start.toISOString()
}

function localDayEnd(date: Date): string {
  const end = new Date(date)
  end.setHours(23, 59, 59, 999)
  return end.toISOString()
}

function buildParams(): LogListParams {
  const params: LogListParams = { page: page.value, page_size: PAGE_SIZE }

  if (filters.model) params.model = filters.model
  if (filters.status) params.status = filters.status
  if (keyword.value.trim()) params.q = keyword.value.trim()

  const tokenRange = TOKEN_RANGES[filters.tokens]
  if (tokenRange?.min !== undefined) params.min_tokens = tokenRange.min
  if (tokenRange?.max !== undefined) params.max_tokens = tokenRange.max

  const today = new Date()
  if (filters.range === 'today') {
    params.start_date = localDayStart(today)
  } else if (filters.range === '7d') {
    params.start_date = localDayStart(new Date(today.getTime() - 6 * DAY_MS))
  } else if (filters.range === '30d') {
    params.start_date = localDayStart(new Date(today.getTime() - 29 * DAY_MS))
  } else if (filters.range === 'custom') {
    if (filters.customStart) {
      params.start_date = localDayStart(parseLocalDate(filters.customStart))
    }
    if (filters.customEnd) {
      params.end_date = localDayEnd(parseLocalDate(filters.customEnd))
    }
  }

  return params
}

async function load(): Promise<void> {
  if (!Number.isInteger(projectId.value)) {
    loadError.value = '项目地址不正确'
    return
  }
  if (rangeError.value) return

  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchLogs(projectId.value, buildParams())
    items.value = data.items
    total.value = data.total
    knownModels.value = [
      ...new Set([...knownModels.value, ...data.items.map((item) => item.model)]),
    ].sort()
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

let reloadTimer: number | undefined

// 所有变化都走这一条防抖路径：搜索框是逐字触发的，下拉框晚 300ms 再发也感觉不到，
// 换来的是只有一处发请求，不会出现两条路径同时触发
function scheduleReload(): void {
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(() => void load(), 300)
}

// 条件变了就回到第一页：停在第 3 页再换一个只有一页结果的条件，看到的会是一片空白
watch([filters, keyword], () => {
  page.value = 1
})

watch([filters, keyword, page], scheduleReload)

function goPage(target: number | '…'): void {
  if (typeof target !== 'number') return
  if (target < 1 || target > pageCount.value || target === page.value) return
  page.value = target
}

function resetFilters(): void {
  filters.model = ''
  filters.status = ''
  filters.range = ''
  filters.customStart = ''
  filters.customEnd = ''
  filters.tokens = ''
  keyword.value = ''
  page.value = 1
  // 条件和页码都由上面的 watch 兜着发请求，本来就没条件可清时它也不会触发，无需额外处理
}

async function openDetail(row: LogListItem): Promise<void> {
  const requestedId = row.id
  selectedId.value = requestedId
  detail.value = null
  detailError.value = ''
  detailLoading.value = true

  try {
    const data = await fetchLogDetail(requestedId)
    // 请求飞在半路时用户又点了别的行，这一份结果已经作废，写进去会把新的盖掉
    if (selectedId.value !== requestedId) return
    detail.value = data
  } catch (error) {
    if (selectedId.value !== requestedId) return
    detailError.value = extractErrorMessage(error)
  } finally {
    if (selectedId.value === requestedId) detailLoading.value = false
  }
}

function closeDetail(): void {
  selectedId.value = null
  detail.value = null
  detailError.value = ''
}

onMounted(load)
</script>

<template>
  <header class="page-head">
    <div>
      <RouterLink class="crumb" :to="{ name: 'home' }">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M15 6l-6 6 6 6" />
        </svg>
        我的项目
      </RouterLink>

      <div class="head-line">
        <h1 class="page-title">{{ project?.name ?? '调用日志' }}</h1>
        <span v-if="project?.tech_stack" class="tag is-mute">{{ project.tech_stack }}</span>
        <span
          v-if="project"
          class="tag"
          :class="{
            'is-warn': projectHealth(project) === 'alert',
            'is-mute': projectHealth(project) === 'unknown',
          }"
        >
          <span class="dot" />
          {{ HEALTH_LABEL[projectHealth(project)] }}
        </span>
      </div>
    </div>
  </header>

  <ProjectTabs :project-id="projectId" />

  <section class="filters">
    <select v-model="filters.model" class="filter-select" aria-label="按模型筛选">
      <option value="">全部模型</option>
      <option v-for="name in knownModels" :key="name" :value="name">{{ name }}</option>
    </select>

    <select v-model="filters.status" class="filter-select" aria-label="按状态筛选">
      <option value="">全部状态</option>
      <option value="success">成功</option>
      <option value="error">失败</option>
    </select>

    <select v-model="filters.range" class="filter-select" aria-label="按时间范围筛选">
      <option value="">全部时间</option>
      <option value="today">今日</option>
      <option value="7d">近 7 天</option>
      <option value="30d">近 30 天</option>
      <option value="custom">自定义范围</option>
    </select>

    <template v-if="filters.range === 'custom'">
      <input v-model="filters.customStart" class="filter-date" type="date" aria-label="开始日期" />
      <span class="filter-dash">至</span>
      <input v-model="filters.customEnd" class="filter-date" type="date" aria-label="结束日期" />
    </template>

    <select v-model="filters.tokens" class="filter-select" aria-label="按 Token 范围筛选">
      <option value="">Token 不限</option>
      <option value="lt1k">小于 1 千</option>
      <option value="1k-10k">1 千 ~ 1 万</option>
      <option value="gt10k">大于 1 万</option>
    </select>

    <div class="filter-search">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
           stroke-linecap="round">
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-3.6-3.6" />
      </svg>
      <input v-model="keyword" type="search" placeholder="搜索提问、回答或模型名" />
    </div>

    <button class="btn btn-ghost btn-sm" type="button" @click="resetFilters">重置</button>

    <p v-if="rangeError" class="filter-error">{{ rangeError }}</p>
  </section>

  <section class="card">
    <div v-if="loading && !items.length" class="loading">
      <span class="spinner" />
      正在加载调用记录…
    </div>

    <div v-else-if="loadError" class="empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v4.5M12 16h.01" />
        </svg>
      </div>
      <div class="empty-title">{{ loadError }}</div>
      <p class="empty-hint">它可能已被删除，或者不属于当前账号</p>
    </div>

    <div v-else-if="!items.length" class="empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 7h16M4 12h16M4 17h9" />
        </svg>
      </div>
      <div class="empty-title">
        {{ hasFilter ? '没有符合条件的记录' : '这个项目还没有调用记录' }}
      </div>
      <p class="empty-hint">
        {{
          hasFilter
            ? '换个条件试试，或者点「重置」看全部'
            : '接入方上报第一条调用之后，这里就会显示'
        }}
      </p>
    </div>

    <template v-else>
      <div class="table-scroll">
        <table class="table log-table" :class="{ 'is-busy': loading }">
          <thead>
            <tr>
              <th style="padding-left: 0">时间</th>
              <th>模型</th>
              <th class="num">输入 Token</th>
              <th class="num">输出 Token</th>
              <th class="num">总 Token</th>
              <th class="num">耗时</th>
              <th class="num">预估费用</th>
              <th style="text-align: right">状态</th>
              <th style="text-align: right; padding-right: 0"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id" @click="openDetail(row)">
              <td class="time" style="padding-left: 0">{{ formatDateTime(row.created_at) }}</td>
              <td class="model">{{ row.model }}</td>
              <td class="num">{{ formatNumber(row.input_tokens) }}</td>
              <td class="num">{{ formatNumber(row.output_tokens) }}</td>
              <td class="num">{{ formatNumber(row.total_tokens) }}</td>
              <td class="num">{{ formatLatency(row.latency) }}</td>
              <td class="num">{{ formatCost(row.estimated_cost) }}</td>
              <td style="text-align: right">
                <span class="tag" :class="{ 'is-bad': row.status === 'error' }">
                  <span class="dot" />
                  {{ row.status === 'error' ? '失败' : '成功' }}
                </span>
              </td>
              <td class="peek">查看 →</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="pager">
        <span class="pager-info">共 {{ total }} 条，每页 {{ PAGE_SIZE }} 条</span>
        <div class="pager-btns">
          <button
            class="pager-btn"
            type="button"
            :disabled="page <= 1"
            @click="goPage(page - 1)"
          >
            上一页
          </button>
          <button
            v-for="(target, index) in visiblePages"
            :key="index"
            class="pager-btn"
            :class="{ 'is-on': target === page }"
            type="button"
            :disabled="target === '…'"
            @click="goPage(target)"
          >
            {{ target }}
          </button>
          <button
            class="pager-btn"
            type="button"
            :disabled="page >= pageCount"
            @click="goPage(page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
  </section>

  <AppDrawer v-if="selectedId !== null" title="调用详情" @close="closeDetail">
    <div v-if="detailLoading" class="loading">
      <span class="spinner" />
      正在加载详情…
    </div>

    <div v-else-if="detailError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ detailError }}</span>
    </div>

    <template v-else-if="detail">
      <div class="drawer-meta">
        <span class="tag" :class="{ 'is-bad': detail.status === 'error' }">
          <span class="dot" />
          {{ detail.status === 'error' ? '失败' : '成功' }}
        </span>
        <span class="tag is-mute">{{ detail.model }}</span>
        <span class="tag is-mute">{{ detail.provider }}</span>
        <span class="drawer-time">{{ formatDateTimeFull(detail.created_at) }}</span>
      </div>

      <div class="metrics">
        <div class="metric">
          <div class="metric-name">输入 Token</div>
          <div class="metric-value">{{ formatNumber(detail.input_tokens) }}</div>
        </div>
        <div class="metric">
          <div class="metric-name">输出 Token</div>
          <div class="metric-value">{{ formatNumber(detail.output_tokens) }}</div>
        </div>
        <div class="metric">
          <div class="metric-name">总 Token</div>
          <div class="metric-value">{{ formatNumber(detail.total_tokens) }}</div>
        </div>
        <div class="metric">
          <div class="metric-name">响应时间</div>
          <div class="metric-value">{{ formatLatency(detail.latency) }}</div>
        </div>
        <div class="metric">
          <div class="metric-name">预估费用</div>
          <div class="metric-value">{{ formatCost(detail.estimated_cost) }}</div>
        </div>
      </div>

      <template v-if="detail.status === 'error' && detail.error_message">
        <div class="content-label">错误信息</div>
        <div class="content-block is-error">{{ detail.error_message }}</div>
      </template>

      <div class="content-label">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 19V5M5 12l7-7 7 7" />
        </svg>
        用户提问
        <button
          v-if="detail.input"
          type="button"
          class="content-copy"
          :class="{ 'is-done': copied === 'input' }"
          @click="copyRaw('input', detail.input)"
        >
          {{ copied === 'input' ? '已复制' : '复制原文' }}
        </button>
      </div>
      <div v-if="detail.input" class="content-block" :class="{ 'is-json': inputJson !== undefined }">
        <JsonView v-if="inputJson !== undefined" :data="inputJson" root />
        <template v-else>{{ detail.input }}</template>
      </div>
      <div v-else class="content-none">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="M4 10h16" />
        </svg>
        这次调用只上报了指标，没有记录提问内容
      </div>

      <div class="content-label">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 5v14M5 12l7 7 7-7" />
        </svg>
        模型回答
        <button
          v-if="detail.output"
          type="button"
          class="content-copy"
          :class="{ 'is-done': copied === 'output' }"
          @click="copyRaw('output', detail.output)"
        >
          {{ copied === 'output' ? '已复制' : '复制原文' }}
        </button>
      </div>
      <div v-if="detail.output" class="content-block is-out" :class="{ 'is-json': outputJson !== undefined }">
        <JsonView v-if="outputJson !== undefined" :data="outputJson" root />
        <template v-else>{{ detail.output }}</template>
      </div>
      <div v-else class="content-none">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="M4 10h16" />
        </svg>
        这次调用只上报了指标，没有记录回答内容
      </div>
    </template>
  </AppDrawer>
</template>

<style scoped>
.head-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/* --- 筛选栏 ---
   包成白卡：筛选器散在页面底色上时会和下面的表格糊成一片，
   "这是可以操作的一组条件"读不出来 */

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 15px 18px;
  margin-bottom: var(--gutter);
  background: var(--card);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
}

.filter-select,
.filter-date {
  appearance: none;
  padding: 9px 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background-color: var(--card);
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.filter-select {
  padding-right: 34px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%236B7F76' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 11px center;
}

.filter-select:focus,
.filter-date:focus {
  outline: none;
  border-color: var(--green-400);
  box-shadow: 0 0 0 3px var(--green-50);
}

.filter-dash {
  color: var(--faint);
  font-size: var(--fs-sm);
}

.filter-search {
  position: relative;
  flex: 1 1 200px;
  min-width: 180px;
}

.filter-search input {
  width: 100%;
  appearance: none;
  padding: 9px 13px 9px 36px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--card);
  font-family: inherit;
  font-size: var(--fs-sm);
  color: var(--text);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.filter-search input::placeholder {
  color: var(--faint);
}

.filter-search input:focus {
  outline: none;
  border-color: var(--green-400);
  box-shadow: 0 0 0 3px var(--green-50);
}

.filter-search svg {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 15px;
  height: 15px;
  color: var(--faint);
  pointer-events: none;
}

.filter-error {
  flex: 1 0 100%;
  color: var(--red);
  font-size: var(--fs-xs);
}

/* --- 表格 --- */

.table-scroll {
  overflow-x: auto;
}

/* 九列挤不下的屏幕上横向滚动，而不是把列压成一团 */
.log-table {
  min-width: 780px;
}

.log-table tbody tr {
  cursor: pointer;
}

/* 换条件后表格先留在原地变淡，不清空重画：清空会让整页高度跳一下，
   用户在搜索框里连续输入时会一直闪 */
.log-table.is-busy {
  opacity: 0.55;
}

.log-table .time {
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.log-table .model {
  font-weight: 560;
  color: var(--ink);
}

.log-table .peek {
  color: var(--faint);
  font-size: var(--fs-xs);
  text-align: right;
  white-space: nowrap;
}

.log-table tbody tr:hover .peek {
  color: var(--green-500);
}

/* --- 分页 --- */

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding: 16px 2px 2px;
  margin-top: 6px;
  border-top: 1px solid #f1f5f3;
}

.pager-info {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.pager-btns {
  display: flex;
  gap: 4px;
}

.pager-btn {
  min-width: 32px;
  padding: 6px 9px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--card);
  font-family: inherit;
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
  color: var(--muted);
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.pager-btn:hover:not(:disabled) {
  border-color: var(--green-300);
  color: var(--green-600);
}

.pager-btn.is-on {
  background: var(--green-500);
  border-color: var(--green-500);
  color: #fff;
  font-weight: 600;
}

.pager-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* --- 抽屉里的内容 --- */

.drawer-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

.drawer-time {
  color: var(--faint);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}

/* 5 个指标排成 3 + 2：格线画在格子上而不是用 gap 露底色，
   否则第二行空出来的那格会变成一块灰条 */
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
  margin-bottom: 24px;
}

.metric {
  padding: 13px 15px;
}

.metric + .metric {
  border-left: 1px solid var(--line);
}

.metric:nth-child(3n + 1) {
  border-left: 0;
}

.metric:nth-child(n + 4) {
  border-top: 1px solid var(--line);
}

.metric-name {
  color: var(--faint);
  font-size: var(--fs-xs);
  margin-bottom: 5px;
}

.metric-value {
  font-size: var(--fs-lg);
  font-weight: 660;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.content-label {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--fs-sm);
  font-weight: 640;
  color: var(--ink);
  margin-bottom: 8px;
}

.content-label svg {
  width: 15px;
  height: 15px;
  flex: none;
  color: var(--green-500);
}

/* 放在标题行右端而不是内容块里绝对定位：后者会压住内容的第一行。
   静止时只有一行淡灰小字，悬停才浮出胶囊底色 —— 既看得见又不会喧宾夺主 */
.content-copy {
  margin-left: auto;
  padding: 0 9px;
  border: 1px solid transparent;
  border-radius: var(--r-pill);
  background: transparent;
  color: var(--faint);
  /* button 默认不继承字体，不写这行会掉回浏览器的默认字体 */
  font-family: inherit;
  font-size: 11px;
  /* 标题行是 640 的字重，按钮跟着变粗会像是标题的一部分 */
  font-weight: 400;
  line-height: 19px;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s,
    color 0.15s;
}

.content-copy:hover,
.content-copy.is-done {
  border-color: var(--green-300);
  background: var(--card);
  color: var(--green-700);
}

.content-block {
  padding: 13px 15px;
  border-radius: var(--r-md);
  background: #f7faf8;
  border: 1px solid #edf2ef;
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--text);
  /* 模型回答里换行是有意义的，不能当空白折叠掉 */
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 22px;
}

.content-block.is-out {
  background: var(--green-50);
  border-color: var(--green-100);
}

/* JsonView 自己管换行，pre-wrap 会把组件模板里的缩进也当成内容显示出来 */
.content-block.is-json {
  white-space: normal;
  line-height: 1.65;
}

.content-block.is-error {
  background: var(--red-bg);
  border-color: #f2d9d6;
  color: var(--red);
}

.content-none {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 13px 15px;
  border-radius: var(--r-md);
  border: 1px dashed var(--line);
  color: var(--faint);
  font-size: var(--fs-sm);
  margin-bottom: 22px;
}

.content-none svg {
  width: 15px;
  height: 15px;
  flex: none;
}

@media (max-width: 760px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .metric:nth-child(2n + 1) {
    border-left: 0;
  }

  .metric:nth-child(n + 3) {
    border-top: 1px solid var(--line);
  }
}
</style>
