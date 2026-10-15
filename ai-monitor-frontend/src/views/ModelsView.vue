<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { extractErrorMessage } from '@/api/client'
import { createModel, disableModel, fetchModels, updateModel } from '@/api/models'
import AppModal from '@/components/AppModal.vue'
import { useAssistantStore } from '@/stores/assistant'
import type { Model, ModelCreateIn, ModelUpdateIn, UnpricedModel } from '@/types/model'

const message = useMessage()
const assistant = useAssistantStore()

const items = ref<Model[]>([])
// 上报过但没配价格的模型名：它们没有行可挂，在表格上方单独提示
const unpriced = ref<UnpricedModel[]>([])
const loading = ref(true)
const loadError = ref('')

// 新建和编辑共用一个弹窗：editingId 为 null 表示新建，否则是在改哪一条自建模型
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

// 内置模型点「修改」先弹确认 —— 它不能被改（改一次影响全平台的成本基准），
// 但做成 disabled 用户只会来问「为什么不能改」，所以翻译成「建一份自己的副本」
const cloneTarget = ref<Model | null>(null)

const disableTarget = ref<Model | null>(null)
const disabling = ref(false)
const disableError = ref('')

onMounted(load)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const data = await fetchModels()
    items.value = data.items
    unpriced.value = data.unpriced_models
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

// 增删改之后的静默刷新：失败时落到页面的错误卡片上，而不是让用户对着旧列表
// 以为操作没生效
async function reload(): Promise<void> {
  try {
    const data = await fetchModels()
    items.value = data.items
    // 提示条和列表必须一起刷新：刚补完价的那条要立刻从提示里消失
    unpriced.value = data.unpriced_models
  } catch (error) {
    loadError.value = extractErrorMessage(error)
  }
}

// 价格展示去掉尾随 0：后端是 6 位小数，18 会序列化成 18.0。
// null 显示「—」—— 与「价格是 0」是两回事（同 estimated_cost 的 null/0 之分）
function formatPrice(value: number | null): string {
  if (value === null) return '—'
  return String(Number(value.toFixed(6)))
}

// 对齐后端 NUMERIC(12,6) 的口径：非负数、最多 6 位小数。
// 先在前端拦一道，否则用户填完整个表单才被 422 打回来
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
  // 缓存价可以留空（= 没有缓存档），填了才按同样的规则校验
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
  form.cache_hit_price = model.cache_hit_price === null ? '' : String(model.cache_hit_price)
  clearErrors()
  formOpen.value = true
}

// 厂商定价不在平台库里，只能让助手联网搜。问出去的问题里必须带模型名，
// 光问「多少钱」搜不出东西；名字还没填就退回一句通用问法
function askPrice(): void {
  const name = [form.provider.trim(), form.model_name.trim()].filter(Boolean).join(' ')
  assistant.ask(
    name
      ? `${name} 现在的官方 API 定价是多少？请附来源链接。`
      : '常见大模型现在的官方 API 定价是多少？请附来源链接。',
  )
}

function askEdit(model: Model): void {
  if (model.is_builtin) {
    cloneTarget.value = model
    return
  }
  startEdit(model)
}

// 确认框里的「生成副本」：关掉确认框，打开新建表单并预填内置模型的当前值
function startClone(): void {
  const source = cloneTarget.value
  if (!source) return
  cloneTarget.value = null

  openCreate()
  form.provider = source.provider
  form.model_name = source.model_name
  form.input_price = String(source.input_price)
  form.output_price = String(source.output_price)
  form.cache_hit_price = source.cache_hit_price === null ? '' : String(source.cache_hit_price)
}

// 提示条里的模型名：点了就打开新建表单并把名字填好 —— 名字必须一字不差，
// 让用户手抄反而是最容易出错的一步。价格仍然留空，由用户核对后填
function prefillModel(modelName: string): void {
  openCreate()
  // 必须先 openCreate 再填名：它会清空表单，顺序反了名字会被清掉
  form.model_name = modelName
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
      await createModel(payload)
      message.success('模型已创建')
    } else {
      const current = items.value.find((item) => item.id === editingId.value)
      if (!current) return

      // 只装真正动过的字段：后端按「没传就不动」处理，把整份表单原样发过去，
      // 反而会把用户没碰过的字段当成新值写回去
      const payload: ModelUpdateIn = {}
      if (provider !== current.provider) payload.provider = provider
      if (name !== current.model_name) payload.model_name = name
      if (inputPrice !== current.input_price) payload.input_price = inputPrice
      if (outputPrice !== current.output_price) payload.output_price = outputPrice
      // 缓存价比的是「表单算出来的值」而不是字符串是否为空：
      // 传 null 是「清空缓存档」，与「没传」在语义上是两回事
      if (cachePrice !== current.cache_hit_price) payload.cache_hit_price = cachePrice

      if (!Object.keys(payload).length) {
        // 打开编辑框什么都没改就点保存：没有字段要发，直接收起来，不必空跑一趟网络
        formOpen.value = false
        return
      }

      await updateModel(current.id, payload)
      message.success('模型已更新')
    }

    formOpen.value = false
    await reload()
  } catch (error) {
    formError.value = extractErrorMessage(error)
  } finally {
    saving.value = false
  }
}

function askDisable(model: Model): void {
  disableError.value = ''
  disableTarget.value = model
}

async function confirmDisable(): Promise<void> {
  if (!disableTarget.value || disabling.value) return
  disabling.value = true
  disableError.value = ''

  try {
    await disableModel(disableTarget.value.id)
    disableTarget.value = null
    await reload()
    message.success('模型已禁用')
  } catch (error) {
    disableError.value = extractErrorMessage(error)
  } finally {
    disabling.value = false
  }
}
</script>

<template>
  <header class="page-head">
    <div>
      <h1 class="page-title">模型管理</h1>
      <p class="page-sub">
        价格用于估算成本。内置模型是全平台的基准价，改动会生成一份属于你自己的副本
      </p>
    </div>
    <div class="page-actions">
      <button class="btn btn-primary" type="button" @click="openCreate">新建模型</button>
    </div>
  </header>

  <!-- 行动号召，不是通知：点模型名就打开预填好的新建表单，价格由用户核对后填 -->
  <div v-if="!loadError && unpriced.length" class="note is-warn unpriced-note">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v4.5M12 16h.01" />
    </svg>
    <div>
      <p>有 {{ unpriced.length }} 个上报过的模型还没有可用的价格，它们产生的调用成本只能记成「—」。点模型名补上价格：</p>
      <ul class="unpriced-list">
        <li v-for="item in unpriced" :key="item.model_name">
          <button type="button" class="unpriced-link" @click="prefillModel(item.model_name)">
            {{ item.model_name }}
          </button>
          <span class="unpriced-projects">{{ item.used_by_projects.join('、') }} 在用</span>
        </li>
      </ul>
    </div>
  </div>

  <div v-if="loading && !items.length" class="loading">
    <span class="spinner" />
    正在加载模型…
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
        <h2 class="card-title">全部模型</h2>
        <p class="card-hint">
          价格单位：元 / 百万 Token；「在用的项目」按上报数据里的模型名匹配
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
      <div class="empty-title">还没有可用的模型</div>
      <p class="empty-hint">新建一个，接入方上报后成本才有价可依</p>
    </div>

    <div v-else class="table-scroll">
      <table class="table model-table">
        <thead>
          <tr>
            <th style="padding-left: 0">模型</th>
            <th>Provider</th>
            <th class="num">输入价</th>
            <th class="num">输出价</th>
            <th class="num">缓存价</th>
            <th>来源</th>
            <th>在用的项目</th>
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
              <span class="tag" :class="{ 'is-mute': row.is_builtin }">
                {{ row.is_builtin ? '内置' : '自建' }}
              </span>
            </td>
            <td class="used">
              {{ row.used_by_projects.length ? row.used_by_projects.join('、') : '—' }}
            </td>
            <td class="row-actions">
              <button class="btn btn-ghost btn-sm" type="button" @click="askEdit(row)">
                修改
              </button>
              <!-- 内置模型不显示「禁用」：后端只认自建，给了按钮也只会是 404 -->
              <button
                v-if="!row.is_builtin"
                class="btn btn-ghost btn-sm"
                type="button"
                @click="askDisable(row)"
              >
                禁用
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>

  <AppModal
    v-if="formOpen"
    :title="editingId === null ? '新建模型' : '修改模型'"
    :subtitle="
      editingId === null
        ? '模型名要和接入方上报的 model 字段一字不差，成本才匹配得上'
        : '只会保存真正改动过的字段'
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
        <label class="field-label" for="model-provider">Provider</label>
        <input
          id="model-provider"
          v-model="form.provider"
          class="input"
          :class="{ 'is-invalid': fieldErrors.provider }"
          type="text"
          placeholder="如 deepseek、openai"
        />
        <span v-if="fieldErrors.provider" class="field-error">{{ fieldErrors.provider }}</span>
      </div>

      <div class="field">
        <label class="field-label" for="model-name">模型名</label>
        <input
          id="model-name"
          v-model="form.model_name"
          class="input"
          :class="{ 'is-invalid': fieldErrors.model_name }"
          type="text"
          placeholder="如 deepseek-chat"
        />
        <span v-if="fieldErrors.model_name" class="field-error">
          {{ fieldErrors.model_name }}
        </span>
        <span v-else class="field-hint">与上报数据里的 model 字段完全一致才会匹配到价格</span>
      </div>

      <div class="field-row">
        <div class="field">
          <label class="field-label" for="model-input-price">输入价</label>
          <input
            id="model-input-price"
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
          <label class="field-label" for="model-output-price">输出价</label>
          <input
            id="model-output-price"
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

      <!-- 只负责问、拿到答案由用户自己填：助手没有任何写工具，硬约束 H4 -->
      <p class="price-ask">
        不确定当前价格？
        <button type="button" class="price-ask-link" @click="askPrice">问 AI 价格</button>
        —— 它会联网搜厂商官网，回答里的数字需要你自己核对后填进上面两个框
      </p>

      <div class="field">
        <label class="field-label" for="model-cache-price">缓存命中价</label>
        <input
          id="model-cache-price"
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
    v-if="cloneTarget"
    title="修改内置模型？"
    subtitle="内置模型是全平台共享的基准价，不能直接改"
    @close="cloneTarget = null"
  >
    <p class="modal-text">
      「修改」会生成一份属于你自己的
      <b>{{ cloneTarget.model_name }}</b>
      副本：价格改动只影响你自己项目的成本估算，原内置模型和其他用户都不受影响。
    </p>

    <div class="modal-actions">
      <button class="btn btn-ghost" type="button" @click="cloneTarget = null">取消</button>
      <button class="btn btn-primary" type="button" @click="startClone">生成副本</button>
    </div>
  </AppModal>

  <AppModal
    v-if="disableTarget"
    title="禁用这个模型？"
    subtitle="历史记录的成本不受影响"
    @close="disableTarget = null"
  >
    <div v-if="disableError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ disableError }}</span>
    </div>

    <p class="modal-text">
      禁用后 <b>{{ disableTarget.model_name }}</b> 不再出现在列表里，新的上报也不再按它的价格估算成本；已经记录的调用不受影响。
    </p>

    <div class="modal-actions">
      <button class="btn btn-ghost" type="button" @click="disableTarget = null">取消</button>
      <button class="btn btn-danger" type="button" :disabled="disabling" @click="confirmDisable">
        {{ disabling ? '禁用中…' : '确认禁用' }}
      </button>
    </div>
  </AppModal>
</template>

<style scoped>
.model-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 紧跟在价格两个框下面，负外边距把它往那排靠 —— 这一句是在解释上面两个框怎么填 */
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

/* 页面级提示条：header 的 padding-bottom 管住了它上面的间距，这里补下面 */
.unpriced-note {
  margin-bottom: var(--gutter);
}

/* 让文字块占满 svg 之外的空间，窄屏上正常换行而不是把 svg 挤扁 */
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

/* 与 .price-ask-link 同一种写法：动作藏在句子里，靠下划线表明能点 */
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

.unpriced-projects {
  color: var(--faint);
  font-size: var(--fs-xs);
}

/* 输入价和输出价并排：它们是同一类东西的两个数，竖排会把弹窗拉长 */
.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.table-scroll {
  overflow-x: auto;
  margin-top: 14px;
}

/* 八列在窄屏上挤不下时横向滚动，而不是把列压成一团 */
.model-table {
  min-width: 880px;
}

.model-table .model-name {
  font-weight: 560;
  color: var(--ink);
}

.model-table .provider {
  color: var(--muted);
}

/* 没有缓存档的「—」压到最浅，和真实数字拉开层次 */
.model-table .is-none {
  color: var(--faint);
}

.model-table .used {
  color: var(--muted);
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 按钮是 inline-flex，不 nowrap 的话窄屏上「禁 用」会被拆成两行 */
.row-actions {
  text-align: right;
  white-space: nowrap;
}

.row-actions .btn + .btn {
  margin-left: 8px;
}

.modal-text {
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--text);
}

.modal-text b {
  color: var(--ink);
  font-weight: 650;
}

.empty-action {
  margin-top: 16px;
}
</style>
