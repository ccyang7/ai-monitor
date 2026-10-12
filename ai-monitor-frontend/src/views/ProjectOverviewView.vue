<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchCostStats, fetchModelStats, fetchOverview, fetchTokenStats } from '@/api/stats'
import BriefingBar from '@/components/BriefingBar.vue'
import ModelUsageChart from '@/components/ModelUsageChart.vue'
import ProjectHeader from '@/components/ProjectHeader.vue'
import ProjectTabs from '@/components/ProjectTabs.vue'
import StatCard from '@/components/StatCard.vue'
import TimeRangePicker from '@/components/TimeRangePicker.vue'
import TrendChart from '@/components/TrendChart.vue'
import {
  formatCompact,
  formatCost,
  formatDelta,
  formatLatency,
  formatNumber,
} from '@/utils/format'
import {
  DEFAULT_RANGE,
  OVERVIEW_MAX_DAYS,
  buildTimeRange,
  rangeError,
  rangeLabel,
} from '@/utils/timerange'
import type { Granularity, TimeRangeValue } from '@/utils/timerange'
import type { AnomalyCounts } from '@/types/anomaly'
import type { CostsOut, ModelsOut, OverviewOut, TokensOut } from '@/types/stats'

const props = defineProps<{ projectId: string }>()

const projectId = computed(() => Number(props.projectId))

const range = ref<TimeRangeValue>({ ...DEFAULT_RANGE })
const overview = ref<OverviewOut | null>(null)
const tokens = ref<TokensOut | null>(null)
const costs = ref<CostsOut | null>(null)
const models = ref<ModelsOut | null>(null)
const loading = ref(false)
const loadError = ref('')

// 记住上一次真正加载用的粒度，而不是从 range 实时推导：自定义范围没填全时
// buildTimeRange 返回 null，实时推导会让卡片上的文案和已经画出来的图对不上
const granularity = ref<Granularity>('day')

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
      // 自定义：单独一天时上一区间就是前一天，跨多天才叫「上一周期」
      return granularity.value === 'hour' ? '较前一日' : '较上一周期'
  }
})

const callsDelta = computed(() =>
  formatDelta(summary.value?.calls ?? null, previous.value?.calls ?? null),
)
const tokensDelta = computed(() =>
  formatDelta(summary.value?.total_tokens ?? null, previous.value?.total_tokens ?? null),
)
const costDelta = computed(() =>
  formatDelta(summary.value?.cost ?? null, previous.value?.cost ?? null),
)
const latencyDelta = computed(() =>
  formatDelta(summary.value?.avg_latency ?? null, previous.value?.avg_latency ?? null),
)

// 图吃掉「一屏减掉页面其余部分」的高度。460 是顶栏 + tabs + 统计卡片 + 留白的实测值，
// 写死一个像素高度的话，屏幕矮的人要滚动才看得见 x 轴，屏幕高的人图又小得空荡
const chartHeight = 'clamp(260px, calc(100vh - 460px), 520px)'

const ZERO_COUNTS: AnomalyCounts = { token: 0, latency: 0, error: 0, error_rate: 0 }

const anomalyCounts = computed(() => overview.value?.anomaly_counts ?? ZERO_COUNTS)
const baselineAvailable = computed(() => overview.value?.baseline_available ?? false)

// 简报条上的「数据更新于」：每次取数成功时更新，不是页面挂载时
const loadedAt = ref<number | null>(null)

type Chip = { text: string; tone: 'warn' | 'bad' | 'mute' } | null

// 角标的规则：有话要说才挂。挂了「正常」就等于承诺这张卡上的数字被检查过，
// 而预估成本这一项 V1 根本没有对应的判定 —— 给它发合格证比不给更糟
const callsChip = computed<Chip>(() => {
  const failed = anomalyCounts.value.error + anomalyCounts.value.error_rate
  return failed > 0 ? { text: `异常 ${failed}`, tone: 'bad' } : null
})

const tokensChip = computed<Chip>(() => {
  if (anomalyCounts.value.token > 0) {
    return { text: `偏高 ${anomalyCounts.value.token}`, tone: 'warn' }
  }
  // 基线没攒够时这张卡上什么都没量过，那几格 0 不代表「正常」
  return baselineAvailable.value ? null : { text: '样本不足', tone: 'mute' }
})

const latencyChip = computed<Chip>(() => {
  if (anomalyCounts.value.latency > 0) {
    return { text: `偏慢 ${anomalyCounts.value.latency}`, tone: 'warn' }
  }
  return baselineAvailable.value ? null : { text: '样本不足', tone: 'mute' }
})

// 简报条说「Token 集中在谁」用的是 Token 口径的占比，那就得按 Token 选头名，
// 按调用次数选会和后半句的数字对不上
const topByTokens = computed(() => {
  const items = models.value?.items ?? []
  if (!items.length) return null
  return items.reduce((best, item) =>
    item.token_percentage > best.token_percentage ? item : best,
  )
})

const tokenLabels = computed(() => tokens.value?.series.map((point) => point.date) ?? [])
const tokenValues = computed(() => tokens.value?.series.map((point) => point.total_tokens) ?? [])
const costLabels = computed(() => costs.value?.series.map((point) => point.date) ?? [])
const costValues = computed(() => costs.value?.series.map((point) => point.cost) ?? [])

// 轴的缩写得带货币符号：光看「20」分不清是 20 元还是 20 次调用
const costAxis = (value: number) => `¥${formatCompact(value)}`

const seriesHint = computed(() =>
  granularity.value === 'hour'
    ? '按小时统计，没有调用的小时按 0 计'
    : '按天统计，没有调用的日子按 0 计',
)

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
  // 自定义范围只填了一头、两头填反了、或跨度超上限，都不是有效区间，等用户改好再说
  if (!query || rangeError(range.value, OVERVIEW_MAX_DAYS)) return

  granularity.value = query.granularity
  loading.value = true
  loadError.value = ''
  try {
    const [overviewData, tokenData, costData, modelData] = await Promise.all([
      fetchOverview(projectId.value, query),
      fetchTokenStats(projectId.value, query),
      fetchCostStats(projectId.value, query),
      fetchModelStats(projectId.value, query),
    ])
    overview.value = overviewData
    tokens.value = tokenData
    costs.value = costData
    models.value = modelData
    loadedAt.value = Date.now()
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
      <TimeRangePicker v-model="range" :max-days="OVERVIEW_MAX_DAYS" />
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
    <BriefingBar
      :project-id="projectId"
      :range-label="rangeLabel(range)"
      :calls="summary?.calls ?? null"
      :previous-calls="previous?.calls ?? null"
      :total-tokens="summary?.total_tokens ?? null"
      :top-model="topByTokens?.model ?? null"
      :top-model-share="topByTokens?.token_percentage ?? null"
      :model-count="models?.items.length ?? 0"
      :anomaly-counts="anomalyCounts"
      :baseline-available="baselineAvailable"
      :loaded-at="loadedAt"
    />

    <section class="stats" :class="{ 'is-busy': loading }">
      <StatCard
        label="调用次数"
        :value="formatNumber(summary?.calls ?? 0)"
        :delta="callsDelta.text"
        :delta-dir="callsDelta.dir"
        :hint="compareHint"
        :chip="callsChip"
      />
      <StatCard
        label="总 Token"
        :value="formatNumber(summary?.total_tokens ?? 0)"
        :delta="tokensDelta.text"
        :delta-dir="tokensDelta.dir"
        :hint="compareHint"
        :chip="tokensChip"
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
        :chip="latencyChip"
      />
    </section>

    <section class="card" :class="{ 'is-busy': loading }">
      <div class="card-head">
        <div>
          <h2 class="card-title">Token 用量趋势</h2>
          <p class="card-hint">{{ seriesHint }}</p>
        </div>
      </div>
      <TrendChart
        :labels="tokenLabels"
        :values="tokenValues"
        name="Token"
        type="bar"
        :height="chartHeight"
      />
    </section>

    <div class="charts">
      <section class="card" :class="{ 'is-busy': loading }">
        <div class="card-head">
          <div>
            <h2 class="card-title">成本趋势</h2>
            <p class="card-hint">{{ seriesHint }}</p>
          </div>
        </div>
        <TrendChart
          :labels="costLabels"
          :values="costValues"
          name="成本"
          type="bar"
          :format-value="formatCost"
          :format-axis="costAxis"
          height="220px"
        />
      </section>

      <section class="card" :class="{ 'is-busy': loading }">
        <div class="card-head">
          <div>
            <h2 class="card-title">模型使用占比</h2>
            <p class="card-hint">同一个模型在不同口径下的占比经常不一样</p>
          </div>
        </div>
        <ModelUsageChart :items="models?.items ?? []" />
      </section>
    </div>
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

/* 窄屏时两张卡自动堆成一列 —— 硬写两列的话，半宽的趋势图连 x 轴标签都放不下 */
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: var(--gutter);
  margin-top: var(--gutter);
}
</style>
