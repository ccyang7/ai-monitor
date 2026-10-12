<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchAnomalies } from '@/api/anomaly'
import AnomalySettingsCard from '@/components/AnomalySettingsCard.vue'
import LogDetailDrawer from '@/components/LogDetailDrawer.vue'
import ProjectHeader from '@/components/ProjectHeader.vue'
import ProjectTabs from '@/components/ProjectTabs.vue'
import TimeRangePicker from '@/components/TimeRangePicker.vue'
import { formatDateTimeFull, formatLatency, formatNumber } from '@/utils/format'
import { DEFAULT_RANGE, buildTimeRange, rangeError, rangeLabel } from '@/utils/timerange'
import type { TimeRangeValue } from '@/utils/timerange'
import type { AnomalyItem, AnomalyType, InsufficientBaseline } from '@/types/anomaly'

const props = defineProps<{ projectId: string }>()

const projectId = computed(() => Number(props.projectId))

const PAGE_SIZE = 20

const TYPE_LABELS: Record<AnomalyType, string> = {
  token: 'Token 突增',
  latency: '响应变慢',
  error: '调用失败',
  error_rate: '错误率偏高',
}

// 「全部」用空串表示，不用 null：v-model 绑在按钮的 value 上，空串是现成的「不限」值
const TYPE_OPTIONS: { value: '' | AnomalyType; label: string }[] = [
  { value: '', label: '全部' },
  { value: 'token', label: 'Token' },
  { value: 'latency', label: '响应慢' },
  { value: 'error', label: '失败' },
  { value: 'error_rate', label: '错误率' },
]

const typeFilter = ref<'' | AnomalyType>('')

// 默认近 7 天而不是「全部时间」：基线就是最近 7 天，看别的区间的意义不大。
// 不设跨度上限 —— 这一页是分页列表，不像趋势图那样怕点太密
const range = ref<TimeRangeValue>({ ...DEFAULT_RANGE })

const items = ref<AnomalyItem[]>([])
const total = ref(0)
const page = ref(1)
const baselineAvailable = ref(true)
const insufficient = ref<InsufficientBaseline[]>([])
const loading = ref(false)
const loadError = ref('')

const selectedId = ref<number | null>(null)

// 按类型筛了「失败」还挂着「某个模型的样本不够」，属于答非所问。
// 只有 token / latency 两类和基线有关，其余筛选项下不显示
const showBaselines = computed(
  () => !typeFilter.value || typeFilter.value === 'token' || typeFilter.value === 'latency',
)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const visiblePages = computed<(number | '…')[]>(() => {
  const last = totalPages.value
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

// 一条描述由若干片段拼成，数字那几段要加粗。拼成一个字符串再交给 v-html 会让
// 上报的 error_message 有机会变成标签 —— 数据是别人传上来的，不能当模板用
interface Segment {
  text: string
  strong?: boolean
  tone?: 'warn' | 'bad'
}

interface Card {
  item: AnomalyItem
  title: string
  when: string
  segments: Segment[]
}

// 错误率是窗口判定，描述里给「哪一天」；其余三类都能点进具体某一次调用。
// 模型名单独一个 span 渲染（见模板），不拼进这个字符串 —— 它要加粗，
// 拼成一整句就只能整句一个样式
function describeWhen(item: AnomalyItem): string {
  return item.window ? `${item.window} 全天` : formatDateTimeFull(item.created_at)
}

function describe(item: AnomalyItem): Segment[] {
  const value = item.value ?? 0
  const baseline = item.baseline ?? 0
  const ratio = item.ratio ?? 0

  // 描述不重复标题：标题已经是「调用失败」，描述就直接给错在哪；
  // 「这次调用」四个字同理，一张卡片只有一次调用，主语不用再说
  switch (item.type) {
    case 'token':
      return [
        { text: '用了 ' },
        { text: formatNumber(value), strong: true },
        { text: ' Token，是平时（' },
        { text: formatNumber(baseline), strong: true },
        { text: '）的 ' },
        { text: `${ratio} 倍`, strong: true, tone: 'warn' },
      ]

    case 'latency':
      return [
        { text: '耗时 ' },
        { text: formatLatency(value), strong: true },
        { text: '，是平时（' },
        { text: formatLatency(baseline), strong: true },
        { text: '）的 ' },
        { text: `${ratio} 倍`, strong: true, tone: 'warn' },
      ]

    case 'error':
      return [{ text: item.error_message ?? '上报时没有带错误信息' }]

    case 'error_rate':
      return [
        { text: '错误率 ' },
        { text: `${(value * 100).toFixed(1)}%`, strong: true, tone: 'bad' },
        { text: '，超过参考值 ' },
        { text: `${(baseline * 100).toFixed(0)}%`, strong: true },
      ]
  }
}

const cards = computed<Card[]>(() =>
  items.value.map((item) => ({
    item,
    title: TYPE_LABELS[item.type],
    when: describeWhen(item),
    segments: describe(item),
  })),
)

function isBad(item: AnomalyItem): boolean {
  return item.type === 'error' || item.type === 'error_rate'
}

// 错误率是整天的窗口判定，没有某一次调用可以点开，点了不该有任何反应
function openDetail(item: AnomalyItem): void {
  if (item.log_id === null) return
  selectedId.value = item.log_id
}

async function load(): Promise<void> {
  if (!Number.isInteger(projectId.value)) {
    loadError.value = '项目地址不正确'
    return
  }

  const query = buildTimeRange(range.value)
  // 自定义范围没填全、或两头填反了，都不是有效区间，等用户改好再说
  if (!query || rangeError(range.value)) return

  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchAnomalies(projectId.value, {
      start_date: query.start_date,
      end_date: query.end_date,
      page: page.value,
      page_size: PAGE_SIZE,
      // 空串是「全部」，不能发出去 —— 后端只认四个具体值，发 '' 会 422
      ...(typeFilter.value ? { type: typeFilter.value } : {}),
    })
    items.value = data.items
    total.value = data.total
    baselineAvailable.value = data.baseline_available
    insufficient.value = data.insufficient_baselines
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

function goPage(target: number | '…'): void {
  if (typeof target !== 'number') return
  if (target < 1 || target > totalPages.value || target === page.value) return
  page.value = target
}

// 阈值改完，历史是被重判的 —— 屏幕上这份列表还是旧标准的结果，必须重新拉。
// 回到第 1 页：条数变了，停在第 3 页很可能是一片空白
function reloadAfterSettings(): void {
  if (page.value !== 1) {
    page.value = 1
    return
  }
  void load()
}

// 换条件回到第一页：停在第 3 页再换一个只有一页结果的条件，看到的会是一片空白。
// 页码没变时得自己发请求 —— watch(page) 只在页码真的变了才触发
watch([range, typeFilter], () => {
  if (page.value !== 1) {
    page.value = 1
    return
  }
  void load()
})

watch(page, () => void load())

onMounted(load)
</script>

<template>
  <ProjectHeader :project-id="projectId" fallback-title="异常分析">
    <template #actions>
      <TimeRangePicker v-model="range" />
    </template>
  </ProjectHeader>
  <ProjectTabs :project-id="projectId" />

  <AnomalySettingsCard :project-id="projectId" @saved="reloadAfterSettings" />

  <div class="toolbar">
    <div class="seg">
      <button
        v-for="option in TYPE_OPTIONS"
        :key="option.value"
        class="seg-btn"
        :class="{ 'is-on': typeFilter === option.value }"
        type="button"
        @click="typeFilter = option.value"
      >
        {{ option.label }}
      </button>
    </div>
    <span class="toolbar-note">
      {{ rangeLabel(range) }}有 {{ total }} 条异常
    </span>
  </div>

  <div v-if="loading && !items.length" class="card">
    <div class="loading">
      <span class="spinner" />
      正在分析异常…
    </div>
  </div>

  <div v-else-if="loadError" class="card">
    <div class="empty">
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
  </div>

  <template v-else>
    <div v-if="cards.length" class="anomalies" :class="{ 'is-busy': loading }">
      <!-- 整张卡可点，不挂「查看详情」按钮：每一行右边都站一个按钮，扫一眼全是按钮，
           而类型色条 + 标题已经把「哪类、多严重」说完了 -->
      <article
        v-for="card in cards"
        :key="`${card.item.type}-${card.item.log_id ?? card.item.window}`"
        class="anomaly"
        :class="{ 'is-bad': isBad(card.item), 'is-link': card.item.log_id !== null }"
        :role="card.item.log_id !== null ? 'button' : undefined"
        :tabindex="card.item.log_id !== null ? 0 : undefined"
        @click="openDetail(card.item)"
        @keydown.enter="openDetail(card.item)"
      >
        <div class="anomaly-body">
          <div class="anomaly-top">
            <span class="anomaly-type">{{ card.title }}</span>
            <span class="anomaly-when">{{ card.when }}</span>
            <span v-if="card.item.model" class="anomaly-model">{{ card.item.model }}</span>
          </div>
          <p class="anomaly-desc">
            <template v-for="(segment, index) in card.segments" :key="index">
              <b
                v-if="segment.strong"
                :class="segment.tone ? `is-${segment.tone}` : ''"
              >{{ segment.text }}</b>
              <template v-else>{{ segment.text }}</template>
            </template>
          </p>
        </div>

        <svg v-if="card.item.log_id !== null" class="anomaly-go" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </article>
    </div>

    <div v-else class="card">
      <!-- 样本不够时连图标一起换：一个绿对勾挂在「数据还在积累」上面，
           就是把「还不知道」画成了「没问题」 -->
      <div class="empty" :class="{ 'is-mute': !baselineAvailable }">
        <div class="empty-icon">
          <svg v-if="baselineAvailable" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 6L9 17l-5-5" />
          </svg>
          <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7.5V12l3 2" />
          </svg>
        </div>
        <div class="empty-title">
          {{ baselineAvailable ? '没有发现异常' : '数据还在积累' }}
        </div>
        <p class="empty-hint">
          {{
            baselineAvailable
              ? '这个区间内的调用都在正常范围内'
              : '样本还不够，用量与延迟暂时不做判定 —— 这跟「一切正常」不是一回事'
          }}
        </p>
      </div>
    </div>

    <!-- 只有一页时不画分页器：「上一页 1 下一页」全灰着，占的却是屏幕上的实位 -->
    <div v-if="totalPages > 1" class="pager">
      <span class="pager-info">共 {{ total }} 条，每页 {{ PAGE_SIZE }} 条</span>
      <div class="pager-btns">
        <button class="pager-btn" type="button" :disabled="page <= 1" @click="page -= 1">
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
          :disabled="page >= totalPages"
          @click="page += 1"
        >
          下一页
        </button>
      </div>
    </div>

    <!-- 样本不足：故意和上面的异常卡长得不一样 —— 那几条回答「哪里出了问题」，
         这几条回答「哪里还看不出来」，后者绝不能被画成「一切正常」的样子。
         一个项目一块就够，几行都长得一样，逐行重复解释只是把屏幕填满 -->
    <div v-if="showBaselines && insufficient.length" class="low-sample">
      <div class="low-sample-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M5.6 5.6l12.8 12.8" />
        </svg>
      </div>
      <div class="low-sample-body">
        <div class="low-sample-title">
          {{ insufficient.length }} 个模型样本不够，用量与延迟暂时不判定
        </div>
        <ul class="low-sample-list">
          <li v-for="item in insufficient" :key="item.model">
            <span class="low-sample-model">{{ item.model }}</span>
            <span class="low-sample-when">
              最近 7 天 {{ item.samples }} 次 · 还差 {{ item.required - item.samples }} 次开始判定
            </span>
          </li>
        </ul>
      </div>
    </div>
  </template>

  <LogDetailDrawer :log-id="selectedId" @close="selectedId = null" />
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: var(--gutter);
}

.toolbar-note {
  color: var(--muted);
  font-size: var(--fs-sm);
}

/* 空状态默认是绿的（「没问题」），样本不足时换成中性灰 —— 它要说的是「还不知道」 */
.empty.is-mute .empty-icon {
  background: #f0f2f1;
  color: var(--faint);
}

/* 一眼扫完的卡片流，不是表格：每条要讲清「实际值 vs 基线 vs 倍数」，
   塞进表格会挤成一团 */
.anomalies {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 换条件时先原地变淡，不整块清空重画：清空会让页面高度跳一下 */
.anomalies.is-busy {
  opacity: 0.55;
}

.anomaly {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 15px 18px 15px 20px;
  border-radius: var(--r-lg);
  background: var(--card);
  box-shadow: var(--shadow);
  border-left: 4px solid var(--amber);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

/* 失败和错误率用红色：它们比「用量偏高」更值得立刻处理 */
.anomaly.is-bad {
  border-left-color: var(--red);
}

/* 只有能点开详情的卡片才给悬停反馈。错误率是整天的窗口，没有某一次调用可看，
   把它也画成可点的样子，点下去没反应比不给反馈更让人困惑 */
.anomaly.is-link {
  cursor: pointer;
}

.anomaly.is-link:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(11, 35, 28, 0.05), 0 10px 26px rgba(11, 35, 28, 0.1);
}

.anomaly-body {
  flex: 1;
  min-width: 0;
}

.anomaly-top {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 5px;
}

.anomaly-type {
  font-size: var(--fs-md);
  font-weight: 650;
  color: var(--ink);
}

.anomaly-when {
  color: var(--faint);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}

/* 一行里真正用来扫的是「哪个模型不对劲」，时间只是回想时才看的 ——
   所以模型加粗加深，时间留在 faint 里 */
.anomaly-model {
  font-size: var(--fs-sm);
  font-weight: 650;
  color: var(--text);
}

.anomaly-desc {
  font-size: var(--fs-sm);
  color: var(--muted);
  line-height: 1.7;
  word-break: break-word;
}

.anomaly-desc b {
  color: var(--ink);
  font-weight: 660;
  font-variant-numeric: tabular-nums;
}

.anomaly-desc b.is-warn {
  color: var(--amber);
}

.anomaly-desc b.is-bad {
  color: var(--red);
}

.anomaly-go {
  flex: none;
  width: 17px;
  height: 17px;
  color: var(--faint);
  transition: color 0.18s ease, transform 0.18s ease;
}

.anomaly.is-link:hover .anomaly-go {
  color: var(--ink);
  transform: translateX(2px);
}

/* --- 样本不足 ---
   虚线框 + 纯灰调，一个绿色都不许出现，也不给任何结论 */

.low-sample {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  border-radius: var(--r-md);
  border: 1px dashed var(--line);
  background: #fafbfb;
  margin-top: 14px;
}

.low-sample-icon {
  flex: none;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #f0f2f1;
  color: var(--faint);
}

.low-sample-icon svg {
  width: 17px;
  height: 17px;
}

.low-sample-body {
  flex: 1;
  min-width: 0;
}

.low-sample-title {
  font-size: var(--fs-base);
  font-weight: 650;
  color: var(--ink);
}

.low-sample-list {
  display: flex;
  flex-wrap: wrap;
  gap: 3px 28px;
  margin-top: 6px;
}

.low-sample-list li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.low-sample-model {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--text);
}

.low-sample-when {
  color: var(--faint);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}
</style>
