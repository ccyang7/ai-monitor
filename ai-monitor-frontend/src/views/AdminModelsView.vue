<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  createBuiltinModel,
  deleteBuiltinModel,
  fetchAdminModels,
  fetchGlobalUnpricedModels,
  updateBuiltinModel,
} from '@/api/admin'
import { extractErrorMessage } from '@/api/client'
import AppModal from '@/components/AppModal.vue'
import { useAssistantStore } from '@/stores/assistant'
import type {
  AdminUnpricedModel,
  Model,
  ModelCreateIn,
  ModelUpdateIn,
} from '@/types/model'

const message = useMessage()
const assistant = useAssistantStore()

const items = ref<Model[]>([])
// 全平台有人上报、但没有内置价的模型名：管理员据此决定补哪些
const unpriced = ref<AdminUnpricedModel[]>([])
const loading = ref(true)
const loadError = ref('')

// 新增和编辑共用弹窗：editingId 为 null 表示新增
const formOpen = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const formError = ref('')

const form = reactive({
  provider: '',
  model_name: '',
  input_price: '',
  output_price: '',
  cache_hit_price: '',
})

const fieldErrors = reactive({
  provider: '',
  model_name: '',
  input_price: '',
  output_price: '',
  cache_hit_price: '',
})

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [models, unpricedRows] = await Promise.all([
      fetchAdminModels(),
      fetchGlobalUnpricedModels(),
    ])
    items.value = models
    unpriced.value = unpricedRows
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

// 增改之后静默刷新：失败落到页面错误卡片，而不是让用户对着旧列表以为没生效
async function reload(): Promise<void> {
  try {
    const [models, unpricedRows] = await Promise.all([
      fetchAdminModels(),
      fetchGlobalUnpricedModels(),
    ])
    items.value = models
    // 刚收录的模型必须立刻从未收录列表里消失
    unpriced.value = unpricedRows
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  }
}

// 展示固定两位小数；null 显示「—」—— 与「价格是 0」是两回事
function formatPrice(value: number | null): string {
  if (value === null) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// 对齐后端 NUMERIC(12,6)：非负数、最多 6 位小数，先在前端挡一道
function validatePrice(text: string, label: string): string {
  const trimmed = text.trim()
  if (!trimmed) return `请填写${label}`
  if (!/^\d+(\.\d{1,6})?$/.test(trimmed)) return `${label}要填非负数，最多 6 位小数`
  return ''
}

function clearErrors(): void {
  fieldErrors.provider = ''
  fieldErrors.model_name = ''
  fieldErrors.input_price = ''
  fieldErrors.output_price = ''
  fieldErrors.cache_hit_price = ''
  formError.value = ''
}

function validate(): boolean {
  const provider = form.provider.trim()
  fieldErrors.provider = !provider
    ? '请填写 Provider'
    : provider.length > 50
      ? 'Provider 最多 50 个字符'
      : ''

  const name = form.model_name.trim()
  fieldErrors.model_name = !name
    ? '请填写模型名'
    : name.length > 100
      ? '模型名最多 100 个字符'
      : ''

  fieldErrors.input_price = validatePrice(form.input_price, '输入价')
  fieldErrors.output_price = validatePrice(form.output_price, '输出价')
  // 缓存价留空 = 没有缓存档；填了才按同样规则校验
  fieldErrors.cache_hit_price = form.cache_hit_price.trim()
    ? validatePrice(form.cache_hit_price, '缓存价')
    : ''

  return !Object.values(fieldErrors).some(Boolean)
}

function openCreate(): void {
  editingId.value = null
  form.provider = ''
  form.model_name = ''
  form.input_price = ''
  form.output_price = ''
  form.cache_hit_price = ''
  clearErrors()
  formOpen.value = true
}

function startEdit(model: Model): void {
  editingId.value = model.id
  form.provider = model.provider
  form.model_name = model.model_name
  form.input_price = String(model.input_price)
  form.output_price = String(model.output_price)
  form.cache_hit_price =
    model.cache_hit_price === null ? '' : String(model.cache_hit_price)
  clearErrors()
  formOpen.value = true
}

// 未收录列表点模型名：名字必须一字不差，让管理员手抄反而是最容易出错的一步
function prefillModel(modelName: string): void {
  openCreate()
  // 必须先 openCreate 再填名：它会清空表单，顺序反了名字会被清掉
  form.model_name = modelName
}

const deleteTarget = ref<Model | null>(null)
const deleting = ref(false)
const deleteError = ref('')

function openDelete(model: Model): void {
  deleteTarget.value = model
  deleteError.value = ''
}

function closeDelete(): void {
  if (deleting.value) return
  deleteTarget.value = null
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  if (!target || deleting.value) return
  deleting.value = true
  deleteError.value = ''

  try {
    await deleteBuiltinModel(target.id)
    message.success(`已删除模型 ${target.model_name}`)
    deleteTarget.value = null
    // 未收录列表也要重拉：刚删掉的模型名可能立刻出现在那里
    await reload()
  } catch (error) {
    deleteError.value = extractErrorMessage(error)
  } finally {
    deleting.value = false
  }
}

// 厂商定价不在平台库里，只能让助手联网搜；问句里必须带模型名
function askPrice(): void {
  const name = [form.provider.trim(), form.model_name.trim()].filter(Boolean).join(' ')
  assistant.ask(
    name
      ? `${name} 现在的官方 API 定价是多少？请附来源链接。`
      : '常见大模型现在的官方 API 定价是多少？请附来源链接。',
  )
}

async function save(): Promise<void> {
  if (saving.value) return
  if (!validate()) return
  saving.value = true
  formError.value = ''

  const provider = form.provider.trim()
  const name = form.model_name.trim()
  const inputPrice = Number(form.input_price)
  const outputPrice = Number(form.output_price)
  const cacheRaw = form.cache_hit_price.trim()
  const cachePrice = cacheRaw ? Number(cacheRaw) : null

  try {
    if (editingId.value === null) {
      const payload: ModelCreateIn = {
        provider,
        model_name: name,
        input_price: inputPrice,
        output_price: outputPrice,
        cache_hit_price: cachePrice,
      }
      await createBuiltinModel(payload)
      message.success('内置模型已创建')
    } else {
      const current = items.value.find((item) => item.id === editingId.value)
      if (!current) return

      // 只装真正动过的字段：后端按「没传就不动」处理
      const payload: ModelUpdateIn = {}
      if (provider !== current.provider) payload.provider = provider
      if (name !== current.model_name) payload.model_name = name
      if (inputPrice !== current.input_price) payload.input_price = inputPrice
      if (outputPrice !== current.output_price) payload.output_price = outputPrice
      if (cachePrice !== current.cache_hit_price) payload.cache_hit_price = cachePrice

      if (!Object.keys(payload).length) {
        formOpen.value = false
        return
      }

      await updateBuiltinModel(current.id, payload)
      message.success('内置模型已更新')
    }

    formOpen.value = false
    await reload()
  } catch (error) {
    formError.value = extractErrorMessage(error)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <header class="page-head">
    <div>
      <h1 class="page-title">模型目录</h1>
      <p class="page-sub">
        内置模型是全平台共享的基准价，新增和改价会立刻影响所有项目的成本估算
      </p>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary" type="button" @click="openCreate">
        新增内置模型
      </button>
    </div>
  </header>

  <!-- 数据驱动补录：不靠管理员猜，平台直接告诉他哪些模型正在被上报却没有价 -->
  <div v-if="!loadError && unpriced.length" class="note is-warn unpriced-note">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4.5M12 16h.01" />
    </svg>
    <div>
      <p>有 {{ unpriced.length }} 个模型正在被上报，但全平台都没有内置价。点模型名收录：</p>
      <ul class="unpriced-list">
        <li v-for="item in unpriced" :key="item.model_name">
          <button type="button" class="unpriced-link" @click="prefillModel(item.model_name)">
            {{ item.model_name }}
          </button>
          <span class="unpriced-count">
            {{ item.call_count }} 次调用 · {{ item.user_count }} 个用户在用
          </span>
        </li>
      </ul>
    </div>
  </div>

  <div v-if="loading && !items.length" class="loading">
    <span class="spinner" />
    正在加载模型目录…
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
        <h2 class="card-title">内置模型</h2>
        <p class="card-hint">
          价格单位：元 / 百万 Token；改价只影响之后的上报，历史成本是写入时的快照
        </p>
      </div>
    </div>

    <div v-if="!items.length" class="empty">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3l8 4.6v8.8L12 21l-8-4.6V7.6z" />
          <path d="M12 12l8-4.6M12 12v9M12 12L4 7.4" />
        </svg>
      </div>
      <div class="empty-title">还没有内置模型</div>
      <p class="empty-hint">新增之后，所有用户都能用它估算成本</p>
    </div>

    <div v-else class="table-scroll">
      <table class="table admin-table">
        <thead>
          <tr>
            <th style="padding-left: 0">模型</th>
            <th>Provider</th>
            <th class="num">输入价</th>
            <th class="num">输出价</th>
            <th class="num">缓存价</th>
            <th>状态</th>
            <th style="text-align: right; padding-right: 0">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.id">
            <td class="model-name" style="padding-left: 0">{{ row.model_name }}</td>
            <td class="provider">{{ row.provider }}</td>
            <td class="num">{{ formatPrice(row.input_price) }}</td>
            <td class="num">{{ formatPrice(row.output_price) }}</td>
            <td class="num" :class="{ 'is-none': row.cache_hit_price === null }">
              {{ formatPrice(row.cache_hit_price) }}
            </td>
            <td>
              <span class="tag" :class="{ 'is-mute': row.status === 'disabled' }">
                {{ row.status === 'active' ? '启用中' : '已禁用' }}
              </span>
            </td>
            <td class="row-actions">
              <button class="btn btn-ghost btn-sm" type="button" @click="startEdit(row)">
                修改
              </button>
              <button
                class="btn btn-danger btn-sm row-delete"
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
  </section>

  <AppModal
    v-if="formOpen"
    :title="editingId === null ? '新增内置模型' : '修改内置模型'"
    :subtitle="
      editingId === null
        ? '保存后全平台立刻按这份价格估算成本'
        : '只发送真正改动过的字段；改价只影响之后的上报'
    "
    @close="formOpen = false"
  >
    <div v-if="formError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ formError }}</span>
    </div>

    <form class="model-form" @submit.prevent="save">
      <div class="field">
        <label class="field-label" for="admin-model-provider">Provider</label>
        <input
          id="admin-model-provider"
          v-model="form.provider"
          class="input"
          :class="{ 'is-invalid': fieldErrors.provider }"
          type="text"
          placeholder="如 deepseek、openai"
        />
        <span v-if="fieldErrors.provider" class="field-error">{{ fieldErrors.provider }}</span>
      </div>

      <div class="field">
        <label class="field-label" for="admin-model-name">模型名</label>
        <input
          id="admin-model-name"
          v-model="form.model_name"
          class="input"
          :class="{ 'is-invalid': fieldErrors.model_name }"
          type="text"
          placeholder="如 deepseek-chat"
        />
        <span v-if="fieldErrors.model_name" class="field-error">
          {{ fieldErrors.model_name }}
        </span>
        <span v-else-if="editingId === null" class="field-hint">
          与上报数据里的 model 字段完全一致才会匹配到价格
        </span>
        <span v-else class="field-hint">
          改名等于换匹配键：历史上报的旧名字将不再匹配到这份价格
        </span>
      </div>

      <div class="field-row">
        <div class="field">
          <label class="field-label" for="admin-model-input-price">输入价</label>
          <input
            id="admin-model-input-price"
            v-model="form.input_price"
            class="input"
            :class="{ 'is-invalid': fieldErrors.input_price }"
            type="text"
            inputmode="decimal"
            placeholder="0"
          />
          <span v-if="fieldErrors.input_price" class="field-error">
            {{ fieldErrors.input_price }}
          </span>
        </div>

        <div class="field">
          <label class="field-label" for="admin-model-output-price">输出价</label>
          <input
            id="admin-model-output-price"
            v-model="form.output_price"
            class="input"
            :class="{ 'is-invalid': fieldErrors.output_price }"
            type="text"
            inputmode="decimal"
            placeholder="0"
          />
          <span v-if="fieldErrors.output_price" class="field-error">
            {{ fieldErrors.output_price }}
          </span>
        </div>
      </div>

      <p class="price-ask">
        不确定当前价格？
        <button type="button" class="price-ask-link" @click="askPrice">问 AI 价格</button>
        —— 它会联网搜厂商官网，回答里的数字需要你自己核对后填进上面两个框
      </p>

      <div class="field">
        <label class="field-label" for="admin-model-cache-price">缓存命中价</label>
        <input
          id="admin-model-cache-price"
          v-model="form.cache_hit_price"
          class="input"
          :class="{ 'is-invalid': fieldErrors.cache_hit_price }"
          type="text"
          inputmode="decimal"
          placeholder="留空 = 没有缓存档"
        />
        <span v-if="fieldErrors.cache_hit_price" class="field-error">
          {{ fieldErrors.cache_hit_price }}
        </span>
        <span v-else class="field-hint">
          留空表示没有缓存档（命中的 Token 按输入价算）；真实的免费缓存要填 0
        </span>
      </div>

      <div class="modal-actions">
        <button class="btn btn-ghost" type="button" @click="formOpen = false">取消</button>
        <button class="btn btn-primary" type="submit" :disabled="saving">
          {{ saving ? '保存中…' : editingId === null ? '创建' : '保存' }}
        </button>
      </div>
    </form>
  </AppModal>

  <AppModal
    v-if="deleteTarget"
    title="删除内置模型"
    subtitle="删除后全平台不再按这个模型名估算成本"
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

    <div class="delete-body">
      <p class="delete-warning">
        将删除 <b>{{ deleteTarget.model_name }}</b> 的价格行。已经产生的调用成本是写入时的快照，不会改变；之后再有该模型的上报将匹配不到价格，并会出现在「未收录模型」里。
      </p>
      <div class="modal-actions">
        <button class="btn btn-ghost" type="button" @click="closeDelete">取消</button>
        <button class="btn btn-danger" type="button" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '删除中…' : '永久删除' }}
        </button>
      </div>
    </div>
  </AppModal>
</template>

<style scoped>
.delete-body {
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

.row-delete {
  margin-left: 6px;
}

.model-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.price-ask {
  margin-top: -10px;
  color: var(--faint);
  font-size: var(--fs-xs);
  line-height: 1.7;
}

.price-ask-link {
  padding: 0;
  border: none;
  background: none;
  color: var(--green-600);
  font-family: inherit;
  font-size: var(--fs-xs);
  text-decoration: underline;
  cursor: pointer;
}

.price-ask-link:hover {
  color: var(--green-700);
}

.unpriced-note {
  margin-bottom: var(--gutter);
}

.unpriced-note > div {
  flex: 1;
  min-width: 0;
}

.unpriced-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.unpriced-list li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.unpriced-link {
  padding: 0;
  border: none;
  background: none;
  color: #8a5710;
  font-family: inherit;
  font-size: var(--fs-sm);
  font-weight: 620;
  text-decoration: underline;
  cursor: pointer;
}

.unpriced-link:hover {
  color: var(--green-700);
}

.unpriced-count {
  color: var(--faint);
  font-size: var(--fs-xs);
}

.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.table-scroll {
  overflow-x: auto;
  margin-top: 14px;
}

.admin-table {
  min-width: 760px;
}

.admin-table .model-name {
  font-weight: 560;
  color: var(--ink);
}

.admin-table .provider {
  color: var(--muted);
}

.admin-table .is-none {
  color: var(--faint);
}

.row-actions {
  text-align: right;
  white-space: nowrap;
}

.empty-action {
  margin-top: 16px;
}
</style>
