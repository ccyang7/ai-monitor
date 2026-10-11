<script setup lang="ts">
import { computed, ref } from 'vue'

// root 只由最外层传一次，递归调用不传 —— 根层不画左侧竖线，
// 否则整块内容左边会挂一条贯穿到底的线，看着像渲染残迹
const props = defineProps<{ data: unknown; root?: boolean }>()

// 接进来的 prompt 动辄两千字，铺开会把整块内容淹掉
const COLLAPSE_AT = 200

const expanded = ref(false)
const collapsible = computed(
  () => typeof props.data === 'string' && props.data.length > COLLAPSE_AT,
)
const charCount = computed(() => (typeof props.data === 'string' ? props.data.length : 0))
const shown = computed(() => {
  if (typeof props.data !== 'string') return ''
  if (!collapsible.value || expanded.value) return props.data
  return props.data.slice(0, COLLAPSE_AT) + '…'
})

// 之所以缓存成 computed 而不是在模板里直接调函数：模板里的函数调用每次重渲染都会重跑，
// 而且 v-if 和 v-for 各调一次 —— 这份数据要算两次，白算
const entries = computed<{ key: string; value: unknown }[]>(() => {
  const data = props.data
  if (data === null || typeof data !== 'object' || Array.isArray(data)) return []
  return Object.entries(data as Record<string, unknown>).map(([key, value]) => ({ key, value }))
})

// 空的 {} 和 [] 渲染成一个词，跟在 key 后面读起来更顺，不该另起一行
function isBranch(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0
  return typeof value === 'object' && value !== null && Object.keys(value).length > 0
}
</script>

<template>
  <template v-if="Array.isArray(data)">
    <span v-if="data.length === 0" class="jv-punct">[]</span>
    <div v-else class="jv-nest" :class="{ 'is-root': root }">
      <div
        v-for="(item, index) in data"
        :key="index"
        class="jv-entry"
        :class="{ 'is-branch': isBranch(item) }"
      >
        <span v-if="isBranch(item)" class="jv-index">{{ index }}:</span>
        <span v-else class="jv-dot">·</span>
        <JsonView :data="item" />
      </div>
    </div>
  </template>

  <template v-else-if="data !== null && typeof data === 'object'">
    <span v-if="entries.length === 0" class="jv-punct">{}</span>
    <div v-else class="jv-nest" :class="{ 'is-root': root }">
      <div
        v-for="entry in entries"
        :key="entry.key"
        class="jv-entry"
        :class="{ 'is-branch': isBranch(entry.value) }"
      >
        <span class="jv-key">{{ entry.key }}<span class="jv-punct">:</span></span>
        <JsonView :data="entry.value" />
      </div>
    </div>
  </template>

  <span v-else-if="typeof data === 'string'" class="jv-string">{{ shown }}</span>
  <span v-else-if="typeof data === 'number'" class="jv-number">{{ data }}</span>
  <span v-else-if="typeof data === 'boolean'" class="jv-bool">{{ data }}</span>
  <span v-else class="jv-null">null</span>

  <button v-if="collapsible" type="button" class="jv-toggle" @click="expanded = !expanded">
    {{ expanded ? '收起' : `展开全部 ${charCount} 字` }}
  </button>
</template>

<style scoped>
.jv-nest {
  /* 英文和数字走等宽（缩进和 key 才对得齐）；中文字形不在等宽字体里，
     会自动落到后面的中文字体上，所以中文不会变成方块挤成一坨 */
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'PingFang SC', 'Microsoft YaHei',
    'Hiragino Sans GB', sans-serif;
  flex: 1;
  /* flex 子项默认 min-width:auto，长字符串会把它撑破而不是换行 */
  min-width: 0;
  padding-left: 11px;
  border-left: 1px solid var(--line);
}

.jv-nest.is-root {
  padding-left: 0;
  border-left: none;
}

.jv-entry {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  padding: 1px 0;
}

/* 嵌套的内容另起一行：跟着冒号往下掉，否则深层嵌套的竖线全挤在同一行上 */
.jv-entry.is-branch {
  display: block;
}

.jv-key {
  flex: none;
  color: var(--green-600);
  font-weight: 600;
}

.jv-index {
  flex: none;
  min-width: 11px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}

/* 数组里的标量项用圆点即可，数字下标只在结构复杂的项上才有意义 */
.jv-dot {
  flex: none;
  width: 11px;
  color: var(--faint);
}

.jv-punct {
  flex: none;
  color: var(--faint);
  font-weight: 400;
}

.jv-string {
  flex: 1;
  min-width: 0;
  /* JSON.parse 已经把转义序列还原成真换行符了，这里负责把它们显示出来 */
  white-space: pre-wrap;
  word-break: break-word;
}

.jv-number,
.jv-bool {
  flex: none;
  color: var(--amber);
}

.jv-null {
  flex: none;
  color: var(--faint);
  font-style: italic;
}

.jv-toggle {
  flex: none;
  align-self: flex-start;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-pill);
  background: var(--card);
  color: var(--muted);
  /* button 默认不继承字体，不写这行会掉回浏览器的默认字体 */
  font-family: inherit;
  font-size: 11px;
  line-height: 18px;
  white-space: nowrap;
  cursor: pointer;
}

.jv-toggle:hover {
  border-color: var(--green-400);
  color: var(--green-700);
}
</style>
