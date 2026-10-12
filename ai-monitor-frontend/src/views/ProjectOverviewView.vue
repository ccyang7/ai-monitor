<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchOverview, fetchTokenStats } from '@/api/stats'
import ProjectHeader from '@/components/ProjectHeader.vue'
import ProjectTabs from '@/components/ProjectTabs.vue'
import StatCard from '@/components/StatCard.vue'
import TimeRangePicker from '@/components/TimeRangePicker.vue'
import TrendChart from '@/components/TrendChart.vue'
import {
  formatCompact,
  formatCost,
  formatDateTime,
  formatLatency,
  formatNumber,
} from '@/utils/format'
import { DEFAULT_RANGE, buildTimeRange, rangeError } from '@/utils/timerange'
import type { TimeRangeValue } from '@/utils/timerange'
import type { OverviewOut, TokensOut } from '@/types/stats'

const props = defineProps<{ projectId: string }>()

const projectId = computed(() => Number(props.projectId))

const range = ref<TimeRangeValue>({ ...DEFAULT_RANGE })
const overview = ref<OverviewOut | null>(null)
const tokens = ref<TokensOut | null>(null)
const loading = ref(false)
const loadError = ref('')

const summary = computed(() => overview.value?.summary ?? null)
const previous = computed(() => overview.value?.previous ?? null)

// 后端的环比区间是「上一个等长区间」，不是自然语言里的昨天/上周，
// 所以文案要跟着当前选的粒度走，否则「近 30 天」的卡片上写「较昨日」就串了
const compareHint = computed(() => {
  switch (range.value.preset) {
    case 'today':
      return '较昨日'
    case '7d':
      return '较前 7 天'
    case '30d':
      return '较前 30 天'
    default:
      return '较上一周期'
  }
})

interface Delta {
  text: string
  dir: 'up' | 'down' | 'flat' | 'none'
}

function deltaOf(current: number | null, before: number | null): Delta {
  if (current === null || before === null) return { text: '—', dir: 'none' }
  if (before === 0) {
    // 上一区间是 0，涨跌算不出百分比（除数为零），只能说清楚是「从无到有」
    return current > 0 ? { text: '新增', dir: 'up' } : { text: '—', dir: 'none' }
  }

  const change = ((current - before) / before) * 100
  // 差得极小的时候显示「持平」而不是「↑ 0.0%」，后者看着像个 bug
  if (Math.abs(change) < 0.05) return { text: '持平', dir: 'flat' }

  return change > 0
    ? { text: `↑ ${change.toFixed(1)}%`, dir: 'up' }
    : { text: `↓ ${Math.abs(change).toFixed(1)}%`, dir: 'down' }
}

const callsDelta = computed(() => deltaOf(summary.value?.calls ?? null, previous.value?.calls ?? null))
const tokensDelta = computed(() =>
  deltaOf(summary.value?.total_tokens ?? null, previous.value?.total_tokens ?? null),
)
const costDelta = computed(() => deltaOf(summary.value?.cost ?? null, previous.value?.cost ?? null))
const latencyDelta = computed(() =>
  deltaOf(summary.value?.avg_latency ?? null, previous.value?.avg_latency ?? null),
)

const labels = computed(() => tokens.value?.series.map((point) => point.date) ?? [])
const values = computed(() => tokens.value?.series.map((point) => point.total_tokens) ?? [])

const recent = computed(() => overview.value?.recent_logs ?? [])

// summary 是当前区间的汇总，字段名不叫 today —— 用户选了近 30 天时它不是「今天」
const avgLatencyText = computed(() => {
  const value = summary.value?.avg_latency
  return value === null || value === undefined ? '—' : formatLatency(value)
})

async function load(): Promise<void> {
  if (!Number.isInteger(projectId.value)) {
    loadError.value = '项目地址不正确'
    return
  }

  const query = buildTimeRange(range.value)
  // 自定义范围只填了一头、或两头填反了，都不是有效区间，等用户填完再说
  if (!query || rangeError(range.value)) return

  loading.value = true
  loadError.value = ''
  try {
    const [overviewData, tokenData] = await Promise.all([
      fetchOverview(projectId.value, query),
      fetchTokenStats(projectId.value, query),
    ])
    overview.value = overviewData
    tokens.value = tokenData
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

watch(range, load, { deep: true })

onMounted(load)
</script>

<template>
  <ProjectHeader :project-id="projectId" fallback-title="项目概览">
    <template #actions>
      <TimeRangePicker v-model="range" />
    </template>
  </ProjectHeader>
  <ProjectTabs :project-id="projectId" />

  <div v-if="loading && !overview" class="loading">
    <span class="spinner" />
    正在加载概览…
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

  <template v-else-if="overview">
    <section class="stats" :class="{ 'is-busy': loading }">
      <StatCard
        label="调用次数"
        :value="formatNumber(summary?.calls ?? 0)"
        :delta="callsDelta.text"
        :delta-dir="callsDelta.dir"
        :hint="compareHint"
      />
      <StatCard
        label="总 Token"
        :value="formatCompact(summary?.total_tokens ?? 0)"
        :delta="tokensDelta.text"
        :delta-dir="tokensDelta.dir"
        :hint="compareHint"
      />
      <StatCard
        label="预估成本"
        :value="formatCost(summary?.cost ?? 0)"
        :delta="costDelta.text"
        :delta-dir="costDelta.dir"
        :hint="compareHint"
      />
      <StatCard
        label="平均响应"
        :value="avgLatencyText"
        :delta="latencyDelta.text"
        :delta-dir="latencyDelta.dir"
        :hint="compareHint"
      />
    </section>

    <section class="card" :class="{ 'is-busy': loading }">
      <div class="card-head">
        <div>
          <h2 class="card-title">Token 用量趋势</h2>
          <p class="card-hint">按天统计，没有调用的日子按 0 计</p>
        </div>
      </div>
      <TrendChart :labels="labels" :values="values" name="Token" />
    </section>

    <section class="card" :class="{ 'is-busy': loading }">
      <div class="card-head">
        <h2 class="card-title">最近调用</h2>
        <RouterLink
          v-if="recent.length"
          class="card-link"
          :to="{ name: 'project-logs', params: { projectId } }"
        >
          全部记录 →
        </RouterLink>
      </div>
      <p class="card-hint">这是项目最新发生的调用，不受上方时间筛选影响</p>

      <div v-if="!recent.length" class="empty">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 7h16M4 12h16M4 17h9" />
          </svg>
        </div>
        <div class="empty-title">这个项目还没有调用记录</div>
        <p class="empty-hint">接入方上报第一条调用之后，这里就会显示</p>
      </div>

      <table v-else class="table recent-table">
        <thead>
          <tr>
            <th style="padding-left: 0">时间</th>
            <th>模型</th>
            <th class="num">总 Token</th>
            <th class="num">耗时</th>
            <th style="text-align: right; padding-right: 0">状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in recent" :key="row.id">
            <td class="time" style="padding-left: 0">{{ formatDateTime(row.created_at) }}</td>
            <td class="model">{{ row.model }}</td>
            <td class="num">{{ formatNumber(row.total_tokens) }}</td>
            <td class="num">{{ formatLatency(row.latency) }}</td>
            <td style="text-align: right; padding-right: 0">
              <span class="tag" :class="{ 'is-bad': row.status === 'error' }">
                <span class="dot" />
                {{ row.status === 'error' ? '失败' : '成功' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </template>
</template>

<style scoped>
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(196px, 1fr));
  gap: var(--gutter);
  margin-bottom: var(--gutter);
}

/* 换时间范围时先原地变淡，不整块清空重画：清空会让页面高度跳一下 */
.is-busy {
  opacity: 0.6;
}

.card + .card {
  margin-top: var(--gutter);
}

.card-link {
  color: var(--green-500);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.card-link:hover {
  color: var(--green-600);
}

.recent-table .time {
  color: var(--faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.recent-table .model {
  font-weight: 560;
  color: var(--ink);
}

.recent-table .empty {
  padding: 30px 20px 12px;
}
</style>
