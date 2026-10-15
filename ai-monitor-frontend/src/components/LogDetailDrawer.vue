<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { fetchLogDetail } from '@/api/logs'
import AppDrawer from '@/components/AppDrawer.vue'
import JsonView from '@/components/JsonView.vue'
import { formatCost, formatDateTimeFull, formatLatency, formatNumber } from '@/utils/format'
import { tryParseJson } from '@/utils/json'
import type { LogDetail } from '@/types/log'

// 详情抽屉由调用日志页和异常页共用：两处点进去要看到同一个东西，
// 各写一份的话改一处忘一处，迟早出现「一个页面有复制按钮、另一个没有」
const props = defineProps<{
  // null 表示关闭。用一个 id 而不是 show 布尔值加 id 两个 prop：
  // 后者会出现「show=true 但 id 还是上一条」的中间态
  logId: number | null
}>()

const emit = defineEmits<{ close: [] }>()

const detail = ref<LogDetail | null>(null)
const loading = ref(false)
const error = ref('')

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

async function load(id: number): Promise<void> {
  detail.value = null
  error.value = ''
  loading.value = true

  try {
    const data = await fetchLogDetail(id)
    // 请求飞在半路时用户又点了别的行，这一份结果已经作废，写进去会把新的盖掉
    if (props.logId !== id) return
    detail.value = data
  } catch (cause) {
    if (props.logId !== id) return
    error.value = extractErrorMessage(cause)
  } finally {
    if (props.logId === id) loading.value = false
  }
}

watch(
  () => props.logId,
  (id) => {
    if (id !== null) void load(id)
  },
  { immediate: true },
)
</script>

<template>
  <AppDrawer v-if="logId !== null" title="调用详情" @close="emit('close')">
    <div v-if="loading" class="loading">
      <span class="spinner" />
      正在加载详情…
    </div>

    <div v-else-if="error" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ error }}</span>
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
          <!-- 命中为 0 也显示：否则没法和「平台压根没采到这个字段」区分开 -->
          <div class="metric-note">
            命中 {{ formatNumber(detail.cache_hit_tokens) }} · 未命中
            {{ formatNumber(detail.input_tokens - detail.cache_hit_tokens) }}
          </div>
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

/* 缓存命中明细：压到最小字号附在输入 Token 下面，不抢主数值的视线 */
.metric-note {
  margin-top: 4px;
  font-size: var(--fs-xs);
  color: var(--faint);
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
