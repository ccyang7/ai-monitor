<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { extractErrorMessage } from '@/api/client'
import {
  createApiKey,
  deleteProject,
  fetchApiKeys,
  fetchProject,
  revokeApiKey,
  updateProject,
} from '@/api/projects'
import ApiKeyReveal from '@/components/ApiKeyReveal.vue'
import AppModal from '@/components/AppModal.vue'
import { useProjectsStore } from '@/stores/projects'
import { formatDate, formatDateTime } from '@/utils/format'
import { TECH_STACK_OPTIONS } from '@/utils/project'
import type { ApiKey, ApiKeyCreated } from '@/types/api_key'
import type { Project, ProjectUpdateIn } from '@/types/project'

const props = defineProps<{ projectId: string }>()

const router = useRouter()
const projects = useProjectsStore()
const message = useMessage()

const project = ref<Project | null>(null)
const keys = ref<ApiKey[]>([])
const loading = ref(true)
const loadError = ref('')

const creating = ref(false)
const createError = ref('')
const newKey = ref<ApiKeyCreated | null>(null)

const editing = ref(false)
const saving = ref(false)
const editError = ref('')
const nameError = ref('')

const editForm = reactive({
  name: '',
  description: '',
  tech_stack: '',
})

const keyToRevoke = ref<ApiKey | null>(null)
const revoking = ref(false)
const revokeError = ref('')

const showDelete = ref(false)
const deleteConfirmName = ref('')
const deleting = ref(false)
const deleteError = ref('')

const projectId = computed(() => Number(props.projectId))

const canConfirmDelete = computed(
  () => deleteConfirmName.value.trim() === project.value?.name,
)

// 后端对技术栈只校验长度、不校验取值，直接用 API 建的项目可能是个下拉里没有的名字。
// 不补进去的话 v-model 匹配不到任何 option，select 会显示成一格空白
const stackOptions = computed(() => {
  const current = project.value?.tech_stack
  return current && !TECH_STACK_OPTIONS.includes(current)
    ? [...TECH_STACK_OPTIONS, current]
    : TECH_STACK_OPTIONS
})

onMounted(load)

async function load(): Promise<void> {
  if (!Number.isInteger(projectId.value)) {
    loadError.value = '项目地址不正确'
    loading.value = false
    return
  }

  loading.value = true
  loadError.value = ''
  try {
    const [detail, keyList] = await Promise.all([
      fetchProject(projectId.value),
      fetchApiKeys(projectId.value),
    ])
    project.value = detail
    keys.value = keyList
  } catch (error) {
    // 后端对「不存在」和「不属于你」都返回 404，不区分是刻意的 —— 区分开等于告诉别人
    // 哪些 ID 真实存在
    loadError.value = extractErrorMessage(error)
  } finally {
    loading.value = false
  }
}

function startEdit(): void {
  if (!project.value) return
  editForm.name = project.value.name
  editForm.description = project.value.description ?? ''
  editForm.tech_stack = project.value.tech_stack ?? ''
  editError.value = ''
  nameError.value = ''
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  editError.value = ''
  nameError.value = ''
}

async function saveEdit(): Promise<void> {
  if (!project.value || saving.value) return

  const name = editForm.name.trim()
  if (!name) {
    nameError.value = '项目名称不能为空'
    return
  }
  if (name.length > 100) {
    nameError.value = '项目名称最多 100 个字符'
    return
  }
  nameError.value = ''
  editError.value = ''

  const description = editForm.description.trim()
  const techStack = editForm.tech_stack.trim()

  // 只装真正动过的字段：后端按「没传就不动」处理，把三个字段原样全发过去，
  // 反而会把用户没碰过、只是忘了里面有啥的描述当成新值写回去
  const payload: ProjectUpdateIn = {}
  if (name !== project.value.name) payload.name = name
  if (description !== (project.value.description ?? '')) {
    payload.description = description || null
  }
  if (techStack !== (project.value.tech_stack ?? '')) {
    payload.tech_stack = techStack || null
  }

  // 打开编辑框什么都没改就点保存：没有字段要发，直接收起来，不必空跑一趟网络
  if (!Object.keys(payload).length) {
    editing.value = false
    return
  }

  saving.value = true
  try {
    project.value = await updateProject(projectId.value, payload)
    // 首页和侧边栏显示的是 store 里那份名字，不重新拉的话改完名回到首页还是旧的
    await projects.load(true)
    message.success('项目信息已更新')
    editing.value = false
  } catch (error) {
    editError.value = extractErrorMessage(error)
  } finally {
    saving.value = false
  }
}

async function addKey(): Promise<void> {
  if (creating.value) return
  creating.value = true
  createError.value = ''
  try {
    newKey.value = await createApiKey(projectId.value)
    keys.value = await fetchApiKeys(projectId.value)
  } catch (error) {
    createError.value = extractErrorMessage(error)
  } finally {
    creating.value = false
  }
}

function askRevoke(key: ApiKey): void {
  revokeError.value = ''
  keyToRevoke.value = key
}

async function confirmRevoke(): Promise<void> {
  if (!keyToRevoke.value || revoking.value) return
  revoking.value = true
  revokeError.value = ''

  try {
    await revokeApiKey(keyToRevoke.value.id)
    keys.value = await fetchApiKeys(projectId.value)
    keyToRevoke.value = null
    message.success('Key 已吊销')
  } catch (error) {
    revokeError.value = extractErrorMessage(error)
  } finally {
    revoking.value = false
  }
}

async function confirmDelete(): Promise<void> {
  if (!canConfirmDelete.value || deleting.value) return
  deleting.value = true
  deleteError.value = ''

  try {
    await deleteProject(projectId.value)
    // 提示挂在路由之上的 Provider 上，跳回首页不会把它带走 —— 否则用户会莫名其妙落回首页，
    // 完全不知道刚才那下点没点成功
    message.success('项目已删除')
    await projects.load(true)
    await router.replace({ name: 'home' })
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
      <RouterLink class="crumb" :to="{ name: 'home' }">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M15 6l-6 6 6 6" />
        </svg>
        我的项目
      </RouterLink>

      <div class="head-line">
        <h1 class="page-title">{{ project?.name ?? '项目设置' }}</h1>
        <span v-if="project?.tech_stack" class="tag is-mute">{{ project.tech_stack }}</span>
      </div>
    </div>
  </header>

  <div v-if="loading" class="loading">
    <span class="spinner" />
    正在加载项目…
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
      <p class="empty-hint">它可能已被删除，或者不属于当前账号</p>
      <div class="empty-action">
        <RouterLink class="btn btn-primary" :to="{ name: 'home' }">返回首页</RouterLink>
      </div>
    </div>
  </div>

  <div v-else-if="project" class="settings-grid">
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">项目信息</h2>
          <p class="card-hint">这些信息只影响展示，不影响数据接入</p>
        </div>
        <button v-if="!editing" class="btn btn-ghost btn-sm" type="button" @click="startEdit">
          编辑
        </button>
      </div>

      <form v-if="editing" class="info-form" @submit.prevent="saveEdit">
        <div v-if="editError" class="form-error">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v4.5M12 16h.01" />
          </svg>
          <span>{{ editError }}</span>
        </div>

        <div class="field">
          <label class="field-label" for="edit-name">项目名称</label>
          <input
            id="edit-name"
            v-model="editForm.name"
            class="input"
            :class="{ 'is-invalid': nameError }"
            type="text"
          />
          <span v-if="nameError" class="field-error">{{ nameError }}</span>
          <span v-else class="field-hint">同一个账号下项目名不能重复</span>
        </div>

        <div class="field">
          <label class="field-label" for="edit-desc">项目描述</label>
          <textarea
            id="edit-desc"
            v-model="editForm.description"
            class="textarea"
            rows="2"
            placeholder="选填，一句话说明这个项目是做什么的"
          />
          <span class="field-hint">清空后保存，描述会被移除</span>
        </div>

        <div class="field">
          <label class="field-label" for="edit-stack">技术栈</label>
          <select id="edit-stack" v-model="editForm.tech_stack" class="select">
            <option value="">未填写</option>
            <option v-for="stack in stackOptions" :key="stack" :value="stack">
              {{ stack }}
            </option>
          </select>
        </div>

        <div class="form-actions">
          <button class="btn btn-ghost" type="button" @click="cancelEdit">取消</button>
          <button class="btn btn-primary" type="submit" :disabled="saving">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </form>

      <dl v-else class="info-grid">
        <div class="info-item span-2">
          <dt class="info-label">项目名称</dt>
          <dd class="info-value">{{ project.name }}</dd>
        </div>
        <div class="info-item span-2">
          <dt class="info-label">项目描述</dt>
          <dd class="info-value">{{ project.description || '—' }}</dd>
        </div>
        <div class="info-item">
          <dt class="info-label">技术栈</dt>
          <dd class="info-value">{{ project.tech_stack || '—' }}</dd>
        </div>
        <div class="info-item">
          <dt class="info-label">创建时间</dt>
          <dd class="info-value">{{ formatDateTime(project.created_at) }}</dd>
        </div>
      </dl>
    </section>

    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">API Key</h2>
          <p class="card-hint">接入方用这个 Key 上报调用数据</p>
        </div>
        <button class="btn btn-primary btn-sm" type="button" :disabled="creating" @click="addKey">
          {{ creating ? '生成中…' : '新建 Key' }}
        </button>
      </div>

      <div v-if="createError" class="form-error key-error">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
             stroke-linecap="round">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v4.5M12 16h.01" />
        </svg>
        <span>{{ createError }}</span>
      </div>

      <ApiKeyReveal v-if="newKey" :api-key="newKey.api_key" />

      <div v-if="keys.length" class="keys">
        <div v-for="key in keys" :key="key.id" class="key-row">
          <div class="key-main">
            <div class="key-value">{{ key.key_prefix }}…</div>
            <div class="key-meta">
              <span>创建于 {{ formatDate(key.created_at) }}</span>
            </div>
          </div>
          <span class="tag">
            <span class="dot" />
            使用中
          </span>
          <button class="btn btn-ghost btn-sm" type="button" @click="askRevoke(key)">
            吊销
          </button>
        </div>
      </div>

      <div v-else-if="!newKey" class="empty">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <circle cx="8.5" cy="14.5" r="4.5" />
            <path d="M12 11l8-8M17 4l3 3" />
          </svg>
        </div>
        <div class="empty-title">还没有可用的 Key</div>
        <p class="empty-hint">新建一把，接入方才有凭据上报数据</p>
      </div>
    </section>

    <section class="danger">
      <h2 class="danger-title">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3.5 2.6 20h18.8z" />
          <path d="M12 10v4M12 17.2h.01" />
        </svg>
        删除项目
      </h2>
      <p class="danger-desc">
        删除后，这个项目的所有调用记录、统计数据、API Key 都会被一并删除，无法恢复。
        已经接入的项目会立刻停止上报。
      </p>
      <button class="btn btn-danger" type="button" @click="showDelete = true">
        删除这个项目
      </button>
    </section>
  </div>

  <AppModal
    v-if="keyToRevoke"
    title="吊销这把 Key？"
    subtitle="吊销后立刻生效，正在用它的接入方会上报失败"
    @close="keyToRevoke = null"
  >
    <div v-if="revokeError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ revokeError }}</span>
    </div>

    <p class="modal-text">
      Key <code class="code">{{ keyToRevoke.key_prefix }}…</code> 将被标记为已吊销，
      不能恢复。你需要生成一把新的并更新接入方配置。
    </p>

    <div class="modal-actions">
      <button class="btn btn-ghost" type="button" @click="keyToRevoke = null">取消</button>
      <button class="btn btn-danger" type="button" :disabled="revoking" @click="confirmRevoke">
        {{ revoking ? '吊销中…' : '确认吊销' }}
      </button>
    </div>
  </AppModal>

  <AppModal
    v-if="showDelete"
    title="删除项目"
    subtitle="这个操作不可撤销，请谨慎确认"
    @close="showDelete = false"
  >
    <div v-if="deleteError" class="form-error">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v4.5M12 16h.01" />
      </svg>
      <span>{{ deleteError }}</span>
    </div>

    <div class="modal-body">
      <p class="modal-text">
        项目 <b>{{ project?.name }}</b> 及其所有调用记录、API Key 都会被删除。
        输入项目名以确认。
      </p>
      <div class="field">
        <input
          v-model="deleteConfirmName"
          class="input"
          type="text"
          :placeholder="project?.name"
          autocomplete="off"
        />
      </div>
    </div>

    <div class="modal-actions">
      <button class="btn btn-ghost" type="button" @click="showDelete = false">取消</button>
      <button
        class="btn btn-danger"
        type="button"
        :disabled="!canConfirmDelete || deleting"
        @click="confirmDelete"
      >
        {{ deleting ? '删除中…' : '永久删除' }}
      </button>
    </div>
  </AppModal>
</template>

<style scoped>
.head-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.settings-grid {
  display: flex;
  flex-direction: column;
  gap: var(--gutter);
}

/* --- 项目信息 --- */

.info-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 18px;
}

/* 和弹窗底部那排按钮同一套视觉，只是这里落在卡片里而不是弹窗里 */
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 18px;
  border-top: 1px solid #f1f5f3;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin: 18px 0 0;
}

.info-grid .span-2 {
  grid-column: 1 / -1;
}

.info-item {
  min-width: 0;
}

.info-label {
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 4px;
}

.info-value {
  margin: 0;
  font-size: var(--fs-base);
  color: var(--ink);
  overflow-wrap: anywhere;
}

/* --- API Key --- */

.keys {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 18px;
}

.key-error {
  margin-top: 16px;
}

.key-row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 15px 17px;
  border-radius: var(--r-md);
  border: 1px solid var(--line);
}

.key-main {
  flex: 1;
  min-width: 0;
}

.key-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fs-sm);
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0.01em;
  margin-bottom: 4px;
}

.key-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--faint);
  font-size: var(--fs-xs);
}

/* --- 危险区域 --- */

.danger {
  border-radius: var(--r-lg);
  background: var(--card);
  box-shadow: var(--shadow);
  border: 1px solid #f2d9d6;
  padding: 20px 22px;
}

.danger-title {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: var(--fs-md);
  font-weight: 650;
  color: var(--red);
  margin-bottom: 6px;
}

.danger-title svg {
  width: 17px;
  height: 17px;
  flex: none;
}

.danger-desc {
  font-size: var(--fs-sm);
  line-height: 1.75;
  color: var(--muted);
  margin-bottom: 16px;
  max-width: 62ch;
}

/* --- 弹窗内的说明文字 --- */

.modal-text {
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--text);
}

.modal-text b {
  color: var(--ink);
  font-weight: 650;
}

.code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  padding: 1px 6px;
  border-radius: var(--r-sm);
  background: var(--green-50);
  color: var(--green-700);
}

.empty-action {
  margin-top: 16px;
}

@media (max-width: 820px) {
  .info-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .key-row {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
