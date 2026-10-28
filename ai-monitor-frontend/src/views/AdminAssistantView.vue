<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchAdminAssistantUsage } from '@/api/admin'
import { extractErrorMessage } from '@/api/client'
import StatCard from '@/components/StatCard.vue'
import type { AdminAssistantSummary, AdminAssistantUserRow } from '@/types/assistant'
import { formatLatency, formatNumber, formatRelative } from '@/utils/format'

const PAGE_SIZE = 20

const RANGES = [
  { label: '近 7 天', days: 7 },
  { label: '近 30 天', days: 30 },
  { label: '全部', days: null },
]

const rangeIndex = ref(RANGES.length - 1)
const currentRange = computed(() => RANGES[rangeIndex.value])

const items = ref<AdminAssistantUserRow[]>([])
const summary = ref<AdminAssistantSummary | null>(null)
const total = ref(0)
const page = ref(1)
const loading = ref(true)
const loadError = ref('')

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

const visiblePages = computed<(number | '…')[]>(() => {
  const last = pageCount.value
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

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchAdminAssistantUsage({
      page: page.value,
      page_size: PAGE_SIZE,
      days: currentRange.value.days ?? undefined,
    })
    items.value = data.items
    summary.value = data.summary
    total.value = data.total
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

// 换时间范围回第一页：停在第 3 页再切到只有一页的范围，看到的会是一片空白
watch(rangeIndex, () => {
  page.value = 1
  void load()
})

watch(page, () => void load())

function goPage(target: number | '…'): void {
  if (typeof target !== 'number') return
  if (target < 1 || target > pageCount.value || target === page.value) return
  page.value = target
}

const errorRateHint = computed(() => {
  const data = summary.value
  if (!data || !data.total_chats) return '占提问的 0%'
  return `占提问的 ${Math.round((data.error_count / data.total_chats) * 100)}%`
})
</script>

<template>
  <header class="page-head">
    <div>
      <h1 class="page-title">助手用量</h1>
      <p class="page-sub">
        一次提问记一行：Token 是这一轮所有模型调用的合计；对话正文不落库，这里也看不到
      </p>
    </div>
    <div class="seg" role="group" aria-label="统计范围">
      <button
        v-for="(item, index) in RANGES"
        :key="item.label"
        class="seg-btn"
        :class="{ 'is-on': index === rangeIndex }"
        type="button"
        :disabled="loading"
        @click="rangeIndex = index"
      >
        {{ item.label }}
      </button>
    </div>
  </header>

  <section v-if="summary" class="stats" :class="{ 'is-busy': loading }">
    <StatCard
      label="提问次数"
      :value="formatNumber(summary.total_chats)"
      :hint="currentRange.label"
    />
    <StatCard
      label="总 Token"
      :value="formatNumber(summary.total_tokens)"
      :hint="currentRange.label"
    />
    <StatCard
      label="失败次数"
      :value="formatNumber(summary.error_count)"
      :hint="errorRateHint"
    />
    <StatCard
      label="活跃用户"
      :value="formatNumber(summary.active_users)"
      hint="有使用记录的用户"
    />
  </section>

  <div v-if="loading && !items.length" class="loading">
    <span class="spinner" />
    正在加载用量…
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
      <div class="empty-action">
        <button class="btn btn-ghost btn-sm" type="button" @click="load">重试</button>
      </div>
    </div>
  </div>

  <section v-else class="card">
    <div class="card-head">
      <div>
        <h2 class="card-title">按用户统计</h2>
        <p class="card-hint">
          按提问次数从多到少排；数字来自 {{ currentRange.label }}的记录
        </p>
      </div>
    </div>

    <div v-if="!items.length" class="empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a8.5 8.5 0 1 1-4.1-7.3L21 4l-1 4.2A8.4 8.4 0 0 1 21 12z" />
          <path d="M8.5 12h.01M12 12h.01M15.5 12h.01" />
        </svg>
      </div>
      <div class="empty-title">
        {{ rangeIndex === RANGES.length - 1 ? '还没有人用过助手' : '这个时间段内没有使用记录' }}
      </div>
      <p class="empty-hint">
        助手每完成一次提问就会记一行；换成「全部」也许能看到更早的记录
      </p>
    </div>

    <template v-else>
      <div class="table-scroll">
        <table class="table assistant-table">
          <thead>
            <tr>
              <th style="padding-left: 0">用户</th>
              <th class="num">提问</th>
              <th class="num">Token</th>
              <th class="num">工具调用</th>
              <th class="num">平均耗时</th>
              <th class="num">失败</th>
              <th style="padding-right: 0">最近使用</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.user_id">
              <td style="padding-left: 0">
                <div class="user-cell">
                  <span class="user-avatar">{{ row.username.slice(0, 1).toUpperCase() }}</span>
                  <span class="user-name">{{ row.username }}</span>
                </div>
              </td>
              <td class="num">{{ formatNumber(row.chat_count) }}</td>
              <td class="num">{{ formatNumber(row.total_tokens) }}</td>
              <td class="num">{{ formatNumber(row.tool_call_count) }}</td>
              <td class="num">{{ formatLatency(row.avg_latency) }}</td>
              <td class="num" :class="{ 'is-bad': row.error_count > 0 }">
                {{ formatNumber(row.error_count) }}
              </td>
              <td class="time" style="padding-right: 0">{{ formatRelative(row.last_used_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="pageCount > 1" class="pager">
        <span class="pager-info">共 {{ total }} 位用户，每页 {{ PAGE_SIZE }} 位</span>
        <div class="pager-btns">
          <button
            class="pager-btn"
            type="button"
            :disabled="page <= 1"
            @click="goPage(page - 1)"
          >
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
            :disabled="page >= pageCount"
            @click="goPage(page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(196px, 1fr));
  gap: var(--gutter);
  margin-bottom: var(--gutter);
}

/* 换时间范围时先原地变淡，不整块清空重画 */
.is-busy {
  opacity: 0.6;
}

.user-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-avatar {
  flex: none;
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--green-50);
  color: var(--green-600);
  font-size: var(--fs-xs);
  font-weight: 650;
}

.user-name {
  font-weight: 560;
  color: var(--ink);
}

.assistant-table .is-bad {
  color: var(--red);
  font-weight: 620;
}

.table-scroll {
  overflow-x: auto;
  margin-top: 14px;
}

.assistant-table {
  min-width: 720px;
}

.empty-action {
  margin-top: 16px;
}
</style>
