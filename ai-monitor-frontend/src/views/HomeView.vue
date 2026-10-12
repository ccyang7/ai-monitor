<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { extractErrorMessage } from '@/api/client'
import { createProject } from '@/api/projects'
import ApiKeyReveal from '@/components/ApiKeyReveal.vue'
import AppModal from '@/components/AppModal.vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { formatRelative } from '@/utils/format'
import { HEALTH_LABEL, TECH_STACK_OPTIONS, projectHealth } from '@/utils/project'
import type { ProjectCreated } from '@/types/project'

const auth = useAuthStore()
const projects = useProjectsStore()

const showCreate = ref(false)
const submitting = ref(false)
const createError = ref('')
const nameError = ref('')
const created = ref<ProjectCreated | null>(null)

const form = reactive({
  name: '',
  description: '',
  tech_stack: TECH_STACK_OPTIONS[0],
})

const subtitle = computed(() => {
  const list = projects.items
  if (!list.length) return '还没有项目，创建第一个开始接入'

  const alerts = list.filter((item) => item.has_anomaly).length
  if (alerts) return `${list.length} 个项目，其中 ${alerts} 个有异常需要看一眼`

  const reporting = list.filter((item) => item.last_called_at).length
  if (!reporting) return `${list.length} 个项目，还没有数据上报`

  return `${list.length} 个项目，${reporting} 个在汇报数据`
})

function openCreate(): void {
  showCreate.value = true
}

function closeCreate(): void {
  showCreate.value = false
  created.value = null
  createError.value = ''
  nameError.value = ''
  form.name = ''
  form.description = ''
  form.tech_stack = TECH_STACK_OPTIONS[0]
}

async function submitCreate(): Promise<void> {
  const name = form.name.trim()

  if (!name) {
    nameError.value = '项目名称不能为空'
    return
  }
  if (name.length > 100) {
    nameError.value = '项目名称最多 100 个字符'
    return
  }
  nameError.value = ''
  createError.value = ''
  submitting.value = true

  try {
    created.value = await createProject({
      name,
      description: form.description.trim() || null,
      tech_stack: form.tech_stack || null,
    })
    await projects.load(true)
  } catch (error) {
    createError.value = extractErrorMessage(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <header class="page-head">
    <div>
      <h1 class="page-title">你好，{{ auth.user?.username }} 👋</h1>
      <p class="page-sub">{{ subtitle }}</p>
    </div>
  </header>

  <div class="section-head">
    <h2 class="section-title">我的项目</h2>
  </div>

  <div v-if="projects.loading && !projects.loaded" class="loading">
    <span class="spinner" />
    正在加载项目…
  </div>

  <div v-else class="projects">
    <RouterLink
      v-for="project in projects.items"
      :key="project.id"
      class="project"
      :to="{ name: 'project-overview', params: { projectId: project.id } }"
    >
      <div class="project-top">
        <div class="project-name">{{ project.name }}</div>
        <span
          class="tag"
          :class="{
            'is-warn': projectHealth(project) === 'alert',
            'is-mute': projectHealth(project) === 'unknown',
          }"
        >
          <span class="dot" />
          {{ HEALTH_LABEL[projectHealth(project)] }}
        </span>
      </div>

      <div class="project-stack">{{ project.tech_stack || '未填写技术栈' }}</div>

      <div class="project-foot">
        <span>最近调用 {{ formatRelative(project.last_called_at) }}</span>
        <span class="project-go">
          查看
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 6l6 6-6 6" />
          </svg>
        </span>
      </div>
    </RouterLink>

    <button class="project-new" type="button" @click="openCreate">
      <span class="project-new-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
             stroke-linecap="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </span>
      <span>创建项目</span>
    </button>
  </div>

  <AppModal
    v-if="showCreate"
    :title="created ? '项目已创建' : '创建项目'"
    :subtitle="
      created
        ? '把下面这个 Key 交给你的 AI 项目，它就能开始上报数据'
        : '创建后会同时生成一把上报用的 API Key'
    "
    @close="closeCreate"
  >
    <template v-if="created">
      <ApiKeyReveal :api-key="created.api_key" />
      <div class="modal-actions">
        <button class="btn btn-primary" type="button" @click="closeCreate">知道了</button>
      </div>
    </template>

    <template v-else>
      <form class="modal-body" novalidate @submit.prevent="submitCreate">
        <div v-if="createError" class="form-error">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v4.5M12 16h.01" />
          </svg>
          <span>{{ createError }}</span>
        </div>

        <div class="field">
          <label class="field-label" for="project-name">项目名称</label>
          <input
            id="project-name"
            v-model="form.name"
            class="input"
            :class="{ 'is-invalid': nameError }"
            type="text"
            placeholder="例如：AI知识问答"
          />
          <span v-if="nameError" class="field-error">{{ nameError }}</span>
          <span v-else class="field-hint">同一个账号下项目名不能重复</span>
        </div>

        <div class="field">
          <label class="field-label" for="project-desc">项目描述</label>
          <textarea
            id="project-desc"
            v-model="form.description"
            class="textarea"
            rows="2"
            placeholder="选填，一句话说明这个项目是做什么的"
          />
        </div>

        <div class="field">
          <label class="field-label" for="project-stack">技术栈</label>
          <select id="project-stack" v-model="form.tech_stack" class="select">
            <option v-for="stack in TECH_STACK_OPTIONS" :key="stack" :value="stack">
              {{ stack }}
            </option>
          </select>
        </div>

        <div class="modal-actions">
          <button class="btn btn-ghost" type="button" @click="closeCreate">取消</button>
          <button class="btn btn-primary" type="submit" :disabled="submitting">
            {{ submitting ? '创建中…' : '创建' }}
          </button>
        </div>
      </form>
    </template>
  </AppModal>
</template>

<style scoped>
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 4px 14px;
}

.section-title {
  font-size: var(--fs-lg);
  font-weight: 650;
}

.projects {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(268px, 1fr));
  gap: var(--gutter);
  margin-bottom: 26px;
}

.project {
  display: block;
  background: var(--card);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow);
  padding: 19px 20px 16px;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.project:hover {
  transform: translateY(-2px);
  box-shadow: 0 2px 4px rgba(11, 35, 28, 0.05), 0 10px 26px rgba(11, 35, 28, 0.1);
}

.project-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 3px;
}

.project-name {
  font-size: var(--fs-md);
  font-weight: 650;
  color: var(--ink);
  line-height: 1.4;
  min-width: 0;
  overflow-wrap: anywhere;
}

.project-stack {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.project-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid #f1f5f3;
  font-size: var(--fs-xs);
  color: var(--faint);
}

.project-go {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--green-500);
  font-size: var(--fs-xs);
  font-weight: 600;
}

.project-go svg {
  width: 13px;
  height: 13px;
}

/* 虚线卡片：一眼看出它和真实项目不是一类东西 */
.project-new {
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  min-height: 150px;
  border: 1.5px dashed var(--green-200);
  border-radius: var(--r-lg);
  background: transparent;
  color: var(--green-500);
  font-family: inherit;
  font-size: var(--fs-sm);
  font-weight: 600;
  cursor: pointer;
  transition: background 0.18s ease, border-color 0.18s ease;
}

.project-new:hover {
  background: var(--green-50);
  border-color: var(--green-400);
}

.project-new-icon {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--green-50);
}

.project-new-icon svg {
  width: 18px;
  height: 18px;
}
</style>
