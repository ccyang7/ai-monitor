<script setup lang="ts">
defineProps<{
  label: string
  value: string
  // 环比文案由页面算好传进来（「↑ 32%」「新增」「—」），组件不管怎么算
  delta?: string
  deltaDir?: 'up' | 'down' | 'flat' | 'none'
  hint?: string
  // 状态角标。文案和颜色都由页面定，组件只管画 —— 每张卡该挂什么话
  // 取决于它对应哪一类异常，那是页面才知道的事。
  // 不传就整块不渲染：没有话可说时不挂一个「正常」，别给没检测过的指标发合格证
  chip?: { text: string; tone: 'warn' | 'bad' | 'mute' } | null
}>()
</script>

<template>
  <div class="stat">
    <div class="stat-top">
      <span class="stat-label">{{ label }}</span>
      <span v-if="chip" class="stat-chip" :class="`is-${chip.tone}`">
        {{ chip.text }}
      </span>
    </div>
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

/* 标签行两端分布：角标挂在右侧，不传角标时标签还是贴在左边。
   用 flex 而不是「标签 + 绝对定位的角标」，后者在窄卡片上会和长标签叠字 */
.stat-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
}

.stat-label {
  color: var(--muted);
  font-size: var(--fs-sm);
}

/* 角标只在有话要说时出现，所以它得够显眼：比正文小一号，但底色和字重都往上顶 */
.stat-chip {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--r-pill);
  font-size: 11px;
  font-weight: 650;
  line-height: 1.7;
}

.stat-chip.is-warn {
  background: var(--amber-bg);
  color: var(--amber);
}

.stat-chip.is-bad {
  background: var(--red-bg);
  color: var(--red);
}

/* 「样本不足」既不是好也不是坏，是「不知道」—— 用中性灰，别给它任何告警色 */
.stat-chip.is-mute {
  background: #f0f2f1;
  color: var(--muted);
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
