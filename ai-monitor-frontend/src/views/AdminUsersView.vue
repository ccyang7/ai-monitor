<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { deleteAdminUser, fetchAdminUsers } from '@/api/admin'
import { extractErrorMessage } from '@/api/client'
import AppModal from '@/components/AppModal.vue'
import { useAuthStore } from '@/stores/auth'
import type { AdminUserItem } from '@/types/user'
import { formatDate, formatNumber, formatRelative } from '@/utils/format'

const PAGE_SIZE = 20

const message = useMessage()
const auth = useAuthStore()

const items = ref<AdminUserItem[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')
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
    const data = await fetchAdminUsers({
      page: page.value,
      page_size: PAGE_SIZE,
      q: keyword.value,
    })
    items.value = data.items
    total.value = data.total
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

let reloadTimer: number | undefined

// 搜索框逐字触发，300ms 内只发最后一次 —— 与日志页同一条规矩
function scheduleReload(): void {
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(() => void load(), 300)
}

// 条件变了就回第一页：停在第 3 页再换一个只有一页结果的关键词，看到的会是一片空白
watch(keyword, () => {
  page.value = 1
})

watch([keyword, page], scheduleReload)

function goPage(target: number | '…'): void {
  if (typeof target !== 'number') return
  if (target < 1 || target > pageCount.value || target === page.value) return
  page.value = target
}

// 「尚未接入数据」是项目语境的措辞；对一个用户来说，没上报过就是没上报过
function lastCalledText(iso: string | null): string {
  return iso ? formatRelative(iso) : '从未上报'
}

const deleteTarget = ref<AdminUserItem | null>(null)
const confirmName = ref('')
const deleting = ref(false)
const deleteError = ref('')

// 输对用户名才放手：这是唯一一道能挡住「点错行」的闸门
const canConfirmDelete = computed(
  () =>
    deleteTarget.value !== null &&
    confirmName.value.trim() === deleteTarget.value.username,
)

function openDelete(row: AdminUserItem): void {
  deleteTarget.value = row
  confirmName.value = ''
  deleteError.value = ''
}

function closeDelete(): void {
  if (deleting.value) return
  deleteTarget.value = null
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (!target || !canConfirmDelete.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''

  try {
    await deleteAdminUser(target.id)
    message.success(`已删除用户 ${target.username}`)
    const shouldStepBack = items.value.length === 1 && page.value > 1
    deleteTarget.value = null
    // 删掉本页最后一条就回退一页，否则会停在一个空页上。
    // 改 page 会触发下面的 watcher 重新加载，别再手动调一次 load
    if (shouldStepBack) page.value -= 1
    else void load()
  } catch (error) {
    deleteError.value = extractErrorMessage(error)
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <header class="page-head">
    <div>
      <h1 class="page-title">用户</h1>
      <p class="page-sub">
        只有账号信息与聚合计数 —— 用户的报告内容、项目名称和助手对话都不在这里
      </p>
    </div>
  </header>

  <div v-if="loading && !items.length" class="loading">
    <span class="spinner" />
    正在加载用户…
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
    <div class="card-head user-head">
      <div>
        <h2 class="card-title">全部用户</h2>
        <p class="card-hint">
          共 {{ formatNumber(total) }} 位用户；「最近调用」取的是该用户所有项目的上报时间
        </p>
      </div>
      <input
        v-model="keyword"
        class="input user-search"
        type="search"
        placeholder="搜索用户名"
        aria-label="搜索用户名"
      />
    </div>

    <div v-if="!items.length" class="empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <circle cx="9" cy="8" r="3.5" />
          <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
          <path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M19.5 20a6.5 6.5 0 0 0-3-5.5" />
        </svg>
      </div>
      <div class="empty-title">
        {{ keyword.trim() ? '没有匹配的用户' : '还没有用户' }}
      </div>
      <p v-if="keyword.trim()" class="empty-hint">
        换个用户名试试；搜索只匹配用户名，不搜邮箱
      </p>
    </div>

    <template v-else>
      <div class="table-scroll">
        <table class="table user-table">
          <thead>
            <tr>
              <th style="padding-left: 0">用户</th>
              <th>角色</th>
              <th class="num">项目数</th>
              <th>最近调用</th>
              <th>注册时间</th>
              <th style="text-align: right; padding-right: 0">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in items" :key="row.id">
              <td style="padding-left: 0">
                <div class="user-cell">
                  <span class="user-avatar">{{ row.username.slice(0, 1).toUpperCase() }}</span>
                  <span>
                    <span class="user-name">{{ row.username }}</span>
                    <span class="user-email">{{ row.email || '未填邮箱' }}</span>
                  </span>
                </div>
              </td>
              <td>
                <span class="tag" :class="{ 'is-mute': row.role !== 'admin' }">
                  {{ row.role === 'admin' ? '管理员' : '普通用户' }}
                </span>
              </td>
              <td class="num">{{ formatNumber(row.project_count) }}</td>
              <td :class="{ 'is-none': !row.last_called_at }">
                {{ lastCalledText(row.last_called_at) }}
              </td>
              <td class="time">{{ formatDate(row.created_at) }}</td>
              <td class="row-actions">
                <span v-if="row.id === auth.user?.id" class="is-self">当前账号</span>
                <button
                  v-else
                  class="btn btn-danger btn-sm"
                  type="button"
                  @click="openDelete(row)"
                >
                  删除
                </button>
              </td>
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

  <AppModal
    v-if="deleteTarget"
    title="删除用户"
    subtitle="这个操作不可撤销"
    @close="closeDelete"
  >
    <div v-if="deleteError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ deleteError }}</span>
    </div>

    <form class="delete-form" @submit.prevent="confirmDelete">
      <p class="delete-warning">
        将永久删除 <b>{{ deleteTarget.username }}</b>，以及该用户名下的
        <b>{{ formatNumber(deleteTarget.project_count) }}</b> 个项目、全部 API Key、调用记录、异常标准和助手用量。
      </p>

      <div class="field">
        <label class="field-label" for="admin-user-delete-confirm">
          输入用户名以确认
        </label>
        <input
          id="admin-user-delete-confirm"
          v-model="confirmName"
          class="input"
          type="text"
          autocomplete="off"
          :placeholder="deleteTarget.username"
        />
      </div>

      <div class="modal-actions">
        <button class="btn btn-ghost" type="button" @click="closeDelete">取消</button>
        <button
          class="btn btn-danger"
          type="submit"
          :disabled="!canConfirmDelete || deleting"
        >
          {{ deleting ? '删除中…' : '永久删除' }}
        </button>
      </div>
    </form>
  </AppModal>
</template>

<style scoped>
.delete-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.delete-warning {
  padding: 12px 14px;
  border-radius: var(--r-sm);
  background: var(--red-bg);
  color: var(--text);
  font-size: var(--fs-sm);
  line-height: 1.7;
}

.row-actions {
  text-align: right;
  white-space: nowrap;
}

.is-self {
  color: var(--faint);
  font-size: var(--fs-xs);
}

.user-search {
  width: 220px;
  flex: none;
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
  display: block;
  font-weight: 560;
  color: var(--ink);
}

.user-email {
  display: block;
  color: var(--faint);
  font-size: var(--fs-xs);
}

.user-table .is-none {
  color: var(--faint);
}

.table-scroll {
  overflow-x: auto;
  margin-top: 14px;
}

.user-table {
  min-width: 680px;
}

.empty-action {
  margin-top: 16px;
}
</style>
