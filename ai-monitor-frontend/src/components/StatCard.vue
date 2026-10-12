<script setup lang="ts">
defineProps<{
  label: string
  value: string
  // 环比文案由页面算好传进来（「↑ 32%」「新增」「—」），组件不管怎么算
  delta?: string
  deltaDir?: 'up' | 'down' | 'flat' | 'none'
  hint?: string
}>()
</script>

<template>
  <div class="stat">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">{{ value }}</div>
    <div class="stat-foot">
      <span v-if="delta" class="stat-delta" :class="`is-${deltaDir ?? 'none'}`">
        {{ delta }}
      </span>
      <span class="stat-hint">{{ hint ?? '较上一周期' }}</span>
    </div>
  </div>
</template>

<style scoped>
.stat {
  padding: 17px 19px 15px;
  background: var(--card);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
}

.stat-label {
  color: var(--muted);
  font-size: var(--fs-sm);
  margin-bottom: 7px;
}

.stat-value {
  font-size: var(--fs-2xl);
  font-weight: 680;
  color: var(--ink);
  line-height: 1.15;
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}

.stat-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 9px;
  font-size: var(--fs-xs);
}

/* 涨用绿色、跌用灰色 —— 这是显示选择不是价值判断：成本降了和调用量涨了
   都是「变好」，跟着涨跌上红绿反而会误导。方向靠箭头读，颜色只做视觉锚点 */
.stat-delta {
  font-weight: 640;
  font-variant-numeric: tabular-nums;
}

.stat-delta.is-up {
  color: var(--green-500);
}

.stat-delta.is-down,
.stat-delta.is-flat,
.stat-delta.is-none {
  color: var(--faint);
}

.stat-hint {
  color: var(--faint);
}
</style>
