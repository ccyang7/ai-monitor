<script setup lang="ts">
import { computed } from 'vue'
import { PRESET_OPTIONS, rangeError } from '@/utils/timerange'
import type { RangePreset, TimeRangeValue } from '@/utils/timerange'

const props = defineProps<{
  modelValue: TimeRangeValue
  // 日志页要「全部时间」这一项；概览页的统计接口 start/end 是必填的，给不了
  allowAll?: boolean
  // 自定义范围的跨度上限，不传就是不限
  maxDays?: number
}>()

const emit = defineEmits<{ 'update:modelValue': [TimeRangeValue] }>()

const error = computed(() => rangeError(props.modelValue, props.maxDays))

// 每次整体替换对象而不是改其中的字段：直接改 props 里的字段 Vue 会警告，
// 而且父组件拿到的是同一个引用，watch 不触发
function update(patch: Partial<TimeRangeValue>): void {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}
</script>

<template>
  <select
    class="filter-select"
    aria-label="时间范围"
    :value="modelValue.preset"
    @change="update({ preset: ($event.target as HTMLSelectElement).value as RangePreset })"
  >
    <option v-if="allowAll" value="">全部时间</option>
    <option v-for="option in PRESET_OPTIONS" :key="option.value" :value="option.value">
      {{ option.label }}
    </option>
  </select>

  <template v-if="modelValue.preset === 'custom'">
    <input
      class="filter-date"
      :class="{ 'is-invalid': error }"
      type="date"
      aria-label="开始日期"
      :value="modelValue.customStart"
      @input="update({ customStart: ($event.target as HTMLInputElement).value })"
    />
    <span class="filter-dash">至</span>
    <input
      class="filter-date"
      :class="{ 'is-invalid': error }"
      type="date"
      aria-label="结束日期"
      :value="modelValue.customEnd"
      @input="update({ customEnd: ($event.target as HTMLInputElement).value })"
    />
  </template>

  <p v-if="error" class="filter-error">{{ error }}</p>
</template>
