<script setup lang="ts">
import { computed } from 'vue'
import { changePercent, formatCompact, formatNumber } from '@/utils/format'
import type { AnomalyCounts } from '@/types/anomaly'

// 「先给结论」的那一条：把这一屏的数字说成一句人话。
// 数据全靠页面算好传进来 —— 它只是个说人话的组件，不查数、不算数
const props = defineProps<{
  projectId: number
  rangeLabel: string
  calls: number | null
  previousCalls: number | null
  totalTokens: number | null
  // 按 Token 占比最高的那个模型，没有数据时为 null
  topModel: string | null
  topModelShare: number | null
  modelCount: number
  anomalyCounts: AnomalyCounts
  baselineAvailable: boolean
  // 数据落地的时刻（Date.now()），null 表示还没加载出来
  loadedAt: number | null
}>()

const anomalyTotal = computed(
  () =>
    props.anomalyCounts.token +
    props.anomalyCounts.latency +
    props.anomalyCounts.error +
    props.anomalyCounts.error_rate,
)

// 三类语气：出了失败/错误率是「坏」，只是用量或延迟偏高是「要留意」，
// 样本不够是「还不知道」—— 后者绝不能画成绿色，那等于说「一切正常」
const tone = computed<'ok' | 'warn' | 'bad' | 'mute'>(() => {
  if (props.anomalyCounts.error + props.anomalyCounts.error_rate > 0) return 'bad'
  if (anomalyTotal.value > 0) return 'warn'
  return props.baselineAvailable ? 'ok' : 'mute'
})

const callsChange = computed(() => changePercent(props.calls, props.previousCalls))

// 措辞按「哪几类真的发生了」挑，不按四格全列一遍 —— 说「0 次调用失败」是废话
const CLAUSES: { key: keyof AnomalyCounts; suffix: string }[] = [
  { key: 'error', suffix: ' 次调用失败' },
  { key: 'error_rate', suffix: ' 天的错误率偏高' },
  { key: 'token', suffix: ' 次调用的 Token 明显偏高' },
  { key: 'latency', suffix: ' 次调用响应异常慢' },
]

const clauses = computed(() =>
  CLAUSES.filter((clause) => props.anomalyCounts[clause.key] > 0).map((clause) => ({
    // 类型带上，:key 才不会只靠下标：同一批数据下顺序是固定的，但少了它
    // Vue 在顺序变化时会复用错节点
    key: clause.key,
    count: props.anomalyCounts[clause.key],
    suffix: clause.suffix,
  })),
)

// 占比够高才说「集中在谁」，否则列个模型数 —— 「45% 集中在 A」读起来像 A 独占
const modelClause = computed(() => {
  if (props.modelCount <= 1 || !props.topModel) return ''
  if (props.topModelShare !== null && props.topModelShare >= 70) {
    return `${Math.round(props.topModelShare)}% 集中在 ${props.topModel}`
  }
  return `分布在 ${props.modelCount} 个模型上`
})

const updatedAt = computed(() => {
  if (props.loadedAt === null) return ''
  const at = new Date(props.loadedAt)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `数据更新于 ${pad(at.getHours())}:${pad(at.getMinutes())}`
})
</script>

<template>
  <section class="brief" :class="`is-${tone}`">
    <div class="brief-icon">
      <svg
        v-if="tone === 'bad'"
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <svg
        v-else-if="tone === 'warn'"
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <path d="M12 3.5 2.6 20h18.8z" />
        <path d="M12 10v4M12 17.2h.01" />
      </svg>
      <svg
        v-else-if="tone === 'mute'"
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M5.6 5.6l12.8 12.8" />
      </svg>
      <svg
        v-else
        viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"
      >
        <path d="M4 18V9M10 18V5M16 18v-6M22 18H2" />
      </svg>
    </div>

    <div class="brief-body">
      <p class="brief-text">
        {{ rangeLabel }}调用 <strong>{{ formatNumber(calls ?? 0) }} 次</strong>
        <!-- 环比的三个分句各自成块，不去拼一整条字符串：中文的标点要贴着前一个词，
             拼接时到处补空格/去空格，改一个词就得重新数一遍。
             同一条规矩在模板里还得更严：换行在浏览器里是一个空格，带标点的那几句
             因此一行写完，标点紧贴着前一个标签 —— 换一行，句子里就多一道缝 -->
        <span v-if="callsChange !== null && Math.abs(callsChange) >= 0.05">，比上一周期<span :class="callsChange > 0 ? 'up' : 'down'">{{ callsChange > 0 ? '多 ' : '少 ' }}{{ Math.abs(callsChange).toFixed(1) }}%</span></span>。Token 用了 <strong>{{ formatCompact(totalTokens ?? 0) }}</strong>
        <span v-if="modelClause">，{{ modelClause }}</span>。<span v-if="clauses.length">
          <template v-for="(clause, index) in clauses" :key="clause.key">
            <span v-if="index">、</span>
            <strong>{{ clause.count }}</strong>{{ clause.suffix }}
          </template>，建议看一眼。
          <RouterLink
            class="brief-link"
            :to="{ name: 'project-anomalies', params: { projectId } }"
          >
            查看异常
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 6l6 6-6 6" />
            </svg>
          </RouterLink>
        </span>

        <span v-else-if="tone === 'ok'">没有发现异常。</span>

        <span v-else>数据还在积累，暂时无法判断有没有异常。<RouterLink
          class="brief-link"
          :to="{ name: 'project-anomalies', params: { projectId } }"
        >查看进度<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"
             stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg></RouterLink>
        </span>
      </p>
      <p v-if="updatedAt" class="brief-time">{{ updatedAt }}</p>
    </div>
  </section>
</template>

<style scoped>
.brief {
  position: relative;
  display: flex;
  gap: 14px;
  background: var(--card);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
  padding: 18px 22px 18px 20px;
  margin-bottom: var(--gutter);
  overflow: hidden;
}

/* 左侧色条：一句话的结论用一条色带表达，比再写一行「注意」省字 */
.brief::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  background: var(--green-500);
}

.brief.is-warn::before {
  background: var(--amber);
}

.brief.is-bad::before {
  background: var(--red);
}

/* 「还不知道」用中性灰，和「没问题」的绿明确分开 */
.brief.is-mute::before {
  background: var(--line);
}

.brief-icon {
  flex: none;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--green-50);
  color: var(--green-500);
  margin-top: 1px;
}

.brief.is-warn .brief-icon {
  background: var(--amber-bg);
  color: var(--amber);
}

.brief.is-bad .brief-icon {
  background: var(--red-bg);
  color: var(--red);
}

.brief.is-mute .brief-icon {
  background: #f0f2f1;
  color: var(--faint);
}

.brief-icon svg {
  width: 17px;
  height: 17px;
}

.brief-body {
  min-width: 0;
}

.brief-text {
  font-size: var(--fs-md);
  line-height: 1.7;
  color: var(--text);
}

/* 结论里的数字加重但不换色，颜色留给「好 / 坏」的判断 */
.brief-text strong {
  color: var(--ink);
  font-weight: 660;
  font-variant-numeric: tabular-nums;
}

.brief-text .up {
  color: var(--amber);
  font-weight: 640;
}

.brief-text .down {
  color: var(--green-500);
  font-weight: 640;
}

.brief-time {
  color: var(--faint);
  font-size: var(--fs-xs);
  margin-top: 3px;
}

/* 陈述句里的出口：靠下划线 + 箭头让人读得出「这里能点」，不靠颜色暗示 */
.brief-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-left: 2px;
  color: var(--amber);
  font-weight: 640;
  border-bottom: 1px solid currentColor;
  transition: color 0.15s ease;
}

.brief-link:hover {
  color: var(--green-600);
}

.brief-link svg {
  width: 13px;
  height: 13px;
}
</style>
