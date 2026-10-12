<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatCost, formatNumber } from '@/utils/format'
import type { ModelUsageItem } from '@/types/stats'

const props = defineProps<{ items: ModelUsageItem[] }>()

type Metric = 'calls' | 'tokens' | 'cost'

const METRICS: { key: Metric; label: string }[] = [
  { key: 'calls', label: '调用次数' },
  { key: 'tokens', label: 'Token' },
  { key: 'cost', label: '成本' },
]

const metric = ref<Metric>('calls')

// 百分比和数值都直接取服务端字段：三个口径的占比后端都算好了。
// 前端拿 calls / cost 现算的话，一是违反「金额不做前端运算」，
// 二是三处四舍五入各算各的，几个百分比加起来就不是 100 了
function pick(item: ModelUsageItem, key: Metric): { percent: number; text: string } {
  if (key === 'tokens') {
    return { percent: item.token_percentage, text: formatNumber(item.total_tokens) }
  }
  if (key === 'cost') {
    return { percent: item.cost_percentage, text: formatCost(item.cost) }
  }
  return { percent: item.call_percentage, text: formatNumber(item.calls) }
}

// 换口径就按新口径重排：切到「成本」却还按调用次数的顺序排，
// 条子一长一短没有规律，等于让用户自己找哪根最长
const rows = computed(() =>
  props.items
    .map((item) => {
      const { percent, text } = pick(item, metric.value)
      return { model: item.model, percent, text }
    })
    .sort((a, b) => b.percent - a.percent),
)
</script>

<template>
  <div class="usage">
    <div class="usage-switch">
      <button
        v-for="option in METRICS"
        :key="option.key"
        type="button"
        :class="{ 'is-active': metric === option.key }"
        @click="metric = option.key"
      >
        {{ option.label }}
      </button>
    </div>

    <p v-if="!rows.length" class="usage-empty">这个区间没有调用记录</p>

    <ul v-else class="usage-list">
      <li v-for="row in rows" :key="row.model" class="usage-row">
        <span class="usage-model" :title="row.model">{{ row.model }}</span>
        <span class="usage-track">
          <span class="usage-fill" :style="{ width: `${row.percent}%` }" />
        </span>
        <span class="usage-value">{{ row.text }}</span>
        <span class="usage-percent">{{ row.percent.toFixed(1) }}%</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.usage-switch {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  margin-bottom: 12px;
  background: var(--paper);
  border-radius: var(--r-md);
}

.usage-switch button {
  padding: 5px 11px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--muted);
  font-family: inherit;
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.usage-switch button:hover {
  color: var(--ink);
}

.usage-switch button.is-active {
  background: var(--card);
  color: var(--green-600);
  box-shadow: 0 1px 2px rgba(11, 35, 28, 0.06);
}

.usage-empty {
  padding: 16px 0;
  color: var(--faint);
  font-size: var(--fs-sm);
}

.usage-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.usage-row {
  display: grid;
  grid-template-columns: minmax(0, 132px) 1fr auto 50px;
  align-items: center;
  gap: 12px;
  padding: 7px 0;
}

.usage-model {
  overflow: hidden;
  color: var(--ink);
  font-size: var(--fs-sm);
  font-weight: 560;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.usage-track {
  height: 9px;
  background: var(--paper);
  border-radius: var(--r-pill);
}

.usage-fill {
  display: block;
  height: 100%;
  background: var(--green-500);
  border-radius: var(--r-pill);
  transition: width 0.25s ease;
}

.usage-value {
  color: var(--text);
  font-size: var(--fs-sm);
  font-variant-numeric: tabular-nums;
}

.usage-percent {
  color: var(--faint);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
</style>
