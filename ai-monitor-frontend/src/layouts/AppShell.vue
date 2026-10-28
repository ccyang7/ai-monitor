<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AiAssistant from '@/components/AiAssistant.vue'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { initials } from '@/utils/format'
import { HEALTH_COLOR, projectHealth } from '@/utils/project'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const projects = useProjectsStore()

const activeProjectId = computed(() => {
  const raw = route.params.projectId
  return raw === undefined ? null : Number(raw)
})

onMounted(() => {
  // 401 已由拦截器统一处理，其余错误不该拦住侧边栏渲染
  projects.load().catch(() => undefined)
})

function logout(): void {
  auth.clear()
  projects.reset()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 12h3.5l2.5-6 4 13 2.5-7h7.5" />
          </svg>
        </div>
        <div>
          <div class="brand-name">AI Monitor</div>
          <div class="brand-sub">大模型使用监控</div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink
          class="nav-item"
          :class="{ 'is-active': route.name === 'home' }"
          :to="{ name: 'home' }"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="9" rx="1.6" />
            <rect x="14" y="3" width="7" height="5" rx="1.6" />
            <rect x="14" y="12" width="7" height="9" rx="1.6" />
            <rect x="3" y="16" width="7" height="5" rx="1.6" />
          </svg>
          <span>首页</span>
        </RouterLink>

        <RouterLink
          class="nav-item"
          :class="{ 'is-active': route.name === 'models' }"
          :to="{ name: 'models' }"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l8 4.6v8.8L12 21l-8-4.6V7.6z" />
            <path d="M12 12l8-4.6M12 12v9M12 12L4 7.4" />
          </svg>
          <span>模型管理</span>
        </RouterLink>

        <template v-if="auth.isAdmin">
          <div class="nav-label">管理后台</div>
          <RouterLink
            class="nav-item"
            :class="{ 'is-active': route.name === 'admin-users' }"
            :to="{ name: 'admin-users' }"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <circle cx="9" cy="8" r="3.5" />
              <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
              <path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M19.5 20a6.5 6.5 0 0 0-3-5.5" />
            </svg>
            <span>用户</span>
          </RouterLink>
          <RouterLink
            class="nav-item"
            :class="{ 'is-active': route.name === 'admin-assistant' }"
            :to="{ name: 'admin-assistant' }"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12a8.5 8.5 0 1 1-4.1-7.3L21 4l-1 4.2A8.4 8.4 0 0 1 21 12z" />
              <path d="M8.5 12h.01M12 12h.01M15.5 12h.01" />
            </svg>
            <span>助手用量</span>
          </RouterLink>
          <RouterLink
            class="nav-item"
            :class="{ 'is-active': route.name === 'admin-models' }"
            :to="{ name: 'admin-models' }"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3l7 3v5.5c0 4.5-3 7.9-7 9.5-4-1.6-7-5-7-9.5V6z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
            <span>模型目录</span>
          </RouterLink>
        </template>

        <RouterLink class="nav-item" to="/docs">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 3.5h6a4 4 0 0 1 4 4V21a3 3 0 0 0-3-3H2z" />
            <path d="M22 3.5h-6a4 4 0 0 0-4 4V21a3 3 0 0 1 3-3h7z" />
          </svg>
          <span>帮助文档</span>
        </RouterLink>

        <template v-if="projects.items.length">
          <div class="nav-label">我的项目</div>
          <RouterLink
            v-for="project in projects.items"
            :key="project.id"
            class="nav-item"
            :class="{ 'is-active': project.id === activeProjectId }"
            :to="{ name: 'project-overview', params: { projectId: project.id } }"
          >
            <span class="dot" :style="{ color: HEALTH_COLOR[projectHealth(project)] }" />
            <span class="nav-text">{{ project.name }}</span>
          </RouterLink>
        </template>
      </nav>

      <div class="sidebar-foot">
        <div class="user-chip">
          <div class="avatar">{{ initials(auth.user?.username ?? '') }}</div>
          <div>
            <div class="user-chip-name">{{ auth.user?.username }}</div>
            <div class="user-chip-role">{{ projects.total }} 个项目</div>
          </div>
          <button class="user-chip-quit" type="button" title="退出登录" @click="logout">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                 stroke-linecap="round" stroke-linejoin="round">
              <path d="M15 17l5-5-5-5M20 12H9M12 20H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h6" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <main class="main">
      <slot />
    </main>

    <AiAssistant />
  </div>
</template>
