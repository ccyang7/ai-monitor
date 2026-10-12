<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { extractErrorMessage } from '@/api/client'
import { fetchAnomalySettings, saveAnomalySettings } from '@/api/anomaly'
import type { AnomalySettingsIn, AnomalySettingsOut } from '@/types/anomaly'

// 项目级的异常判定标准。收起时是一行摘要，展开才是一张表单 ——
// 它是偶尔改一次的东西，不该长期占着页面的上半屏
const props = defineProps<{ projectId: number }>()

// 阈值改了历史会被重判，列表得跟着重拉，所以保存完要通知外面
const emit = defineEmits<{ saved: [] }>()

const message = useMessage()

type FieldKey = keyof AnomalySettingsOut

interface FieldSpec {
  key: FieldKey
  label: string
  short: string
  hint: string
  unit: string
  // 界面用「倍」和「百分比」，接口里都是小数（0.2 表示 20%）。
  // 换算只在这三个数上发生，表单里从头到尾只出现用户看得懂的量
  scale: number
  min: number
  max: number
  step: number
}

const FIELDS: FieldSpec[] = [
  {
    key: 'token_anomaly_ratio',
    label: 'Token 突增',
    short: 'Token',
    hint: '单次调用的 Token 超过基线的多少倍算异常',
    unit: '倍',
    scale: 1,
    min: 1.5,
    max: 20,
    step: 0.5,
  },
  {
    key: 'latency_anomaly_ratio',
    label: '响应变慢',
    short: '延迟',
    hint: '单次调用的耗时超过基线的多少倍算异常',
    unit: '倍',
    scale: 1,
    min: 1.5,
    max: 20,
    step: 0.5,
  },
  {
    key: 'error_rate_threshold',
    label: '错误率偏高',
    short: '错误率',
    hint: '某一天的失败占比超过多少算异常',
    unit: '%',
    scale: 100,
    min: 5,
    max: 100,
    step: 1,
  },
]

const settings = ref<AnomalySettingsOut | null>(null)
const loadError = ref('')
const expanded = ref(false)
const saving = ref(false)
const fieldError = ref('')
const saveError = ref('')

const draft = reactive<Record<FieldKey, { follow: boolean; input: DraftValue }>>({
  token_anomaly_ratio: { follow: true, input: '' },
  latency_anomaly_ratio: { follow: true, input: '' },
  error_rate_threshold: { follow: true, input: '' },
})

type DraftValue = string | number

// type="number" 的输入框，Vue 的 v-model 会自动把值转成数字 ——
// 所以这里拿到的可能是 number 也可能是 string，用之前统一成字符串。
// 空输入框会走到 string 那一支（'' 转不成数字，原样留着）
function inputText(input: DraftValue): string {
  return String(input).trim()
}

const customCount = computed(
  () => FIELDS.filter((field) => settings.value?.[field.key].is_custom).length,
)

const summary = computed(() => {
  if (!settings.value) return ''
  return FIELDS.map((field) => {
    const value = toText(settings.value![field.key].value, field.scale)
    return `${field.short} ${withUnit(field, value)}`
  }).join(' · ')
})

// 0.2 × 100 这类乘法偶尔会带出 20.000000000000004 的尾巴，先收一下再显示
function toText(value: number, scale: number): string {
  return String(Number((value * scale).toFixed(2)))
}

// 中文排版：数字和「倍」之间留个空格，「%」紧贴着数字
function withUnit(field: FieldSpec, value: string): string {
  return field.unit === '%' ? `${value}%` : `${value} ${field.unit}`
}

function rangeText(field: FieldSpec): string {
  return field.unit === '%'
    ? `${field.min}% ~ ${field.max}%`
    : `${field.min} ~ ${field.max} 倍`
}

function syncDraft(data: AnomalySettingsOut): void {
  for (const field of FIELDS) {
    draft[field.key] = {
      follow: !data[field.key].is_custom,
      input: toText(data[field.key].value, field.scale),
    }
  }
}

// 关掉「跟随默认」却填了个和默认一样的数，是在把这一项钉死 ——
// 界面上看不出区别，但以后系统改默认时它不会跟着动了，得说一声
function pinnedHint(field: FieldSpec): string {
  const state = draft[field.key]
  const current = settings.value?.[field.key]
  if (!current || state.follow || !inputText(state.input)) return ''

  const typed = Number(state.input)
  const fallback = Number(toText(current.default, field.scale))
  if (!Number.isFinite(typed) || typed !== fallback) return ''

  return `填的和默认值一样，但这一项会被固定住 —— 以后系统调整默认标准，它不会跟着变`
}

async function load(): Promise<void> {
  loadError.value = ''
  try {
    const data = await fetchAnomalySettings(props.projectId)
    settings.value = data
    syncDraft(data)
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  }
}

function toggle(): void {
  expanded.value = !expanded.value
  fieldError.value = ''
  saveError.value = ''
  // 展开时回到库里的那一份：上次改到一半收起来又打开，看到的应该是已保存的状态
  if (expanded.value && settings.value) syncDraft(settings.value)
}

async function save(successText = '判定标准已更新，列表已按新标准重新判定'): Promise<void> {
  if (!settings.value || saving.value) return

  // PUT 是整体替换，payload 一开始就把三项摆全 —— 跟随默认的那项就是 null
  const payload: AnomalySettingsIn = {
    token_anomaly_ratio: null,
    latency_anomaly_ratio: null,
    error_rate_threshold: null,
  }

  for (const field of FIELDS) {
    const state = draft[field.key]
    if (state.follow) continue

    const typed = Number(state.input)
    if (!inputText(state.input) || !Number.isFinite(typed)) {
      fieldError.value = `「${field.label}」还没有填数值`
      return
    }
    // 范围先在前端挡一道，为的是说人话：后端返回的 422 只会写「Input should be
    // less than or equal to 20」，用户看不懂那是哪个输入框的事
    if (typed < field.min || typed > field.max) {
      fieldError.value = `「${field.label}」要在 ${rangeText(field)}之间，超出这个范围就判不出异常了`
      return
    }
    payload[field.key] = typed / field.scale
  }

  fieldError.value = ''
  saveError.value = ''
  saving.value = true
  try {
    const data = await saveAnomalySettings(props.projectId, payload)
    settings.value = data
    syncDraft(data)
    expanded.value = false
    message.success(successText)
    emit('saved')
  } catch (error) {
    saveError.value = extractErrorMessage(error)
  } finally {
    saving.value = false
  }
}

// 全部恢复默认 = 三项都传 null，也就是「三个开关都打开再保存」。
// 直接复用 save()，两条路径各写一遍请求迟早会不一致
async function restoreDefaults(): Promise<void> {
  for (const field of FIELDS) {
    draft[field.key].follow = true
  }
  await save('已恢复为系统默认标准，列表已重新判定')
}

onMounted(load)
</script>

<template>
  <section v-if="loadError" class="std">
    <div class="std-fail">
      <span>判定标准没读出来：{{ loadError }}</span>
      <button class="btn btn-ghost btn-sm" type="button" @click="load">重试</button>
    </div>
  </section>

  <section v-else-if="settings" class="std">
    <button class="std-head" type="button" :aria-expanded="expanded" @click="toggle">
      <span class="std-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 7h8M18 7h2M4 17h2M12 17h8" />
          <circle cx="15" cy="7" r="2.4" />
          <circle cx="9" cy="17" r="2.4" />
        </svg>
      </span>
      <span class="std-name">异常判定标准</span>
      <span class="std-rule">{{ summary }}</span>
      <span class="tag" :class="customCount ? 'is-warn' : 'is-mute'">
        {{ customCount ? `已自定义 ${customCount} 项` : '按系统默认' }}
      </span>
      <svg class="std-chevron" :class="{ 'is-open': expanded }" viewBox="0 0 24 24"
           fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"
           stroke-linejoin="round">
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>

    <div v-if="expanded" class="std-body">
      <p class="std-intro">
        基线 = 同一个模型最近 7 天的平均水平，超过基线若干倍才算异常。三项可以分别调整：开着「跟随默认」的项不写进库里，将来系统调整默认标准时会跟着一起变；关掉开关填了数，这一项就固定成你填的值。
      </p>

      <div v-for="field in FIELDS" :key="field.key" class="std-row">
        <div class="std-row-text">
          <div class="std-row-label">{{ field.label }}</div>
          <p class="std-row-hint">{{ field.hint }}</p>
          <p v-if="pinnedHint(field)" class="std-row-pin">{{ pinnedHint(field) }}</p>
        </div>

        <label class="std-follow">
          <input v-model="draft[field.key].follow" type="checkbox" />
          跟随默认
        </label>

        <div class="std-value">
          <input
            v-model="draft[field.key].input"
            class="input std-input"
            type="number"
            :min="field.min"
            :max="field.max"
            :step="field.step"
            :disabled="draft[field.key].follow"
          />
          <span class="std-unit">{{ field.unit }}</span>
        </div>
      </div>

      <div v-if="fieldError || saveError" class="form-error std-error">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v4.5M12 16h.01" />
        </svg>
        <span>{{ fieldError || saveError }}</span>
      </div>

      <div class="std-foot">
        <p class="std-warn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 16v-4.5M12 8h.01" />
          </svg>
          <span>判定标准是<b>查询时套用</b>的：保存后<b>历史数据会立刻按新标准重判</b>，下面的列表可能一下多出或少了若干条。这是预期行为，不是算错了。</span>
        </p>
        <div class="std-actions">
          <button
            class="btn btn-ghost"
            type="button"
            :disabled="!customCount || saving"
            @click="restoreDefaults"
          >
            全部恢复默认
          </button>
          <button class="btn btn-ghost" type="button" @click="toggle">取消</button>
          <button class="btn btn-primary" type="button" :disabled="saving" @click="save()">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.std {
  background: var(--card);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
  margin-bottom: var(--gutter);
  overflow: hidden;
}

.std-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 20px;
  border: 0;
  background: transparent;
  font-family: inherit;
  font-size: var(--fs-base);
  font-variant-numeric: tabular-nums;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}

.std-head:hover {
  background: var(--green-50);
}

.std-icon {
  flex: none;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: var(--r-sm);
  background: var(--green-50);
  color: var(--green-600);
}

.std-icon svg {
  width: 16px;
  height: 16px;
}

.std-name {
  flex: none;
  font-weight: 640;
  color: var(--ink);
}

.std-rule {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: var(--fs-sm);
}

.std-chevron {
  flex: none;
  margin-left: auto;
  width: 16px;
  height: 16px;
  color: var(--faint);
  transition: transform 0.18s ease;
}

.std-chevron.is-open {
  transform: rotate(180deg);
}

.std-body {
  padding: 2px 20px 18px;
  border-top: 1px solid #f1f5f3;
}

.std-intro {
  margin: 14px 0 6px;
  max-width: 78ch;
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--muted);
}

.std-row {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 13px 0;
}

.std-row + .std-row {
  border-top: 1px solid #f6f8f7;
}

.std-row-text {
  flex: 1;
  min-width: 0;
}

.std-row-label {
  font-size: var(--fs-base);
  font-weight: 640;
  color: var(--ink);
}

.std-row-hint {
  margin-top: 3px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--faint);
}

/* 说得出的语义差别：填了默认值但关掉开关，这一项就从「跟着变」变成「钉住」 */
.std-row-pin {
  margin-top: 5px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--amber);
}

.std-follow {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-sm);
  color: var(--muted);
  white-space: nowrap;
  cursor: pointer;
}

.std-follow input {
  width: 15px;
  height: 15px;
  accent-color: var(--green-500);
  cursor: pointer;
}

.std-value {
  flex: none;
  display: flex;
  align-items: center;
  gap: 7px;
}

.std-input {
  width: 92px;
  padding: 8px 11px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.std-unit {
  width: 1.2em;
  font-size: var(--fs-sm);
  color: var(--muted);
}

.std-error {
  margin-top: 14px;
}

.std-foot {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #f1f5f3;
}

.std-warn {
  flex: 1 1 320px;
  display: flex;
  gap: 8px;
  font-size: var(--fs-sm);
  line-height: 1.75;
  color: var(--muted);
}

.std-warn svg {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 3px;
  color: var(--amber);
}

.std-warn b {
  color: var(--ink);
  font-weight: 650;
}

.std-actions {
  flex: none;
  display: flex;
  gap: 10px;
}

.std-fail {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 20px;
  font-size: var(--fs-sm);
  color: var(--muted);
}

.std-fail .btn {
  margin-left: auto;
}

@media (max-width: 820px) {
  .std-row {
    flex-wrap: wrap;
    gap: 10px;
  }
  .std-row-text {
    flex: 1 1 100%;
  }
  .std-follow {
    margin-right: auto;
  }
}
</style>
