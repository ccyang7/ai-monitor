<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchLogs } from '@/api/logs'
import LogDetailDrawer from '@/components/LogDetailDrawer.vue'
import ProjectHeader from '@/components/ProjectHeader.vue'
import ProjectTabs from '@/components/ProjectTabs.vue'
import TimeRangePicker from '@/components/TimeRangePicker.vue'
import { formatCost, formatDateTime, formatLatency, formatNumber } from '@/utils/format'
import { ALL_TIME, buildTimeRange, rangeError } from '@/utils/timerange'
import type { TimeRangeValue } from '@/utils/timerange'
import type { LogListItem, LogListParams } from '@/types/log'

const props = defineProps<{ projectId: string }>()

const projectId = computed(() => Number(props.projectId))

const PAGE_SIZE = 20

const TOKEN_RANGES: Record<string, { min?: number; max?: number }> = {
  lt1k: { max: 999 },
  '1k-10k': { min: 1000, max: 10000 },
  gt10k: { min: 10001 },
}

const filters = reactive({
  model: '',
  status: '' as '' | 'success' | 'error',
  tokens: '' as '' | 'lt1k' | '1k-10k' | 'gt10k',
})

// 时间范围单独拿着：它和上面的下拉不是一类东西 —— 值是结构化的区间，
// 而且概览页要用同一个选择器，两边必须是同一份换算逻辑
const range = ref<TimeRangeValue>({ ...ALL_TIME })

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

// 详情抽屉自己按 id 取数（异常页也要点进同一个抽屉），这里只管「点开了哪一条」
const selectedId = ref<number | null>(null)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const hasFilter = computed(() =>
  Boolean(
    filters.model ||
      filters.status ||
      range.value.preset ||
      filters.tokens ||
      keyword.value.trim(),
  ),
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

function buildParams(): LogListParams {
  const params: LogListParams = { page: page.value, page_size: PAGE_SIZE }

  if (filters.model) params.model = filters.model
  if (filters.status) params.status = filters.status
  if (keyword.value.trim()) params.q = keyword.value.trim()

  const tokenRange = TOKEN_RANGES[filters.tokens]
  if (tokenRange?.min !== undefined) params.min_tokens = tokenRange.min
  if (tokenRange?.max !== undefined) params.max_tokens = tokenRange.max

  // 换算成年月日之外的绝对时刻是 utils/timerange 的事，这里只管把结果拼进参数
  const query = buildTimeRange(range.value)
  if (query) {
    params.start_date = query.start_date
    params.end_date = query.end_date
  }

  return params
}

async function load(): Promise<void> {
  if (!Number.isInteger(projectId.value)) {
    loadError.value = '项目地址不正确'
    return
  }
  if (rangeError(range.value)) return

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
watch([filters, range, keyword], () => {
  page.value = 1
})

watch([filters, range, keyword, page], scheduleReload)

function goPage(target: number | '…'): void {
  if (typeof target !== 'number') return
  if (target < 1 || target > pageCount.value || target === page.value) return
  page.value = target
}

function resetFilters(): void {
  filters.model = ''
  filters.status = ''
  filters.tokens = ''
  range.value = { ...ALL_TIME }
  keyword.value = ''
  page.value = 1
  // 条件和页码都由上面的 watch 兜着发请求，本来就没条件可清时它也不会触发，无需额外处理
}

onMounted(load)
</script>

<template>
  <ProjectHeader :project-id="projectId" fallback-title="调用日志" />
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

    <TimeRangePicker v-model="range" allow-all />

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
              <th class="num">缓存命中</th>
              <th class="num">输出 Token</th>
              <th class="num">总 Token</th>
              <th class="num">耗时</th>
              <th class="num">预估费用</th>
              <th style="text-align: right">状态</th>
              <th style="text-align: right; padding-right: 0"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id" @click="selectedId = row.id">
              <td class="time" style="padding-left: 0">{{ formatDateTime(row.created_at) }}</td>
              <td class="model">{{ row.model }}</td>
              <td class="num">{{ formatNumber(row.input_tokens) }}</td>
              <td class="num">{{ formatNumber(row.cache_hit_tokens) }}</td>
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

  <LogDetailDrawer :log-id="selectedId" @close="selectedId = null" />
</template>

<style scoped>
/* 搜索框是这一页专有的（模型 / 状态 / 时间 / Token 那几个控件已经抽成通用类了） */

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
</style>
