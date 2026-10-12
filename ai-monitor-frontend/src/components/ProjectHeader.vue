<script setup lang="ts">
import { computed } from 'vue'
import { useProjectsStore } from '@/stores/projects'
import { HEALTH_LABEL, projectHealth } from '@/utils/project'

const props = defineProps<{ projectId: number; fallbackTitle: string }>()

const projects = useProjectsStore()

// 页头要的项目名、技术栈已经在侧边栏那份列表里了，不必再单独拉一次
const project = computed(
  () => projects.items.find((item) => item.id === props.projectId) ?? null,
)
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
        <h1 class="page-title">{{ project?.name ?? fallbackTitle }}</h1>
        <span v-if="project?.tech_stack" class="tag is-mute">{{ project.tech_stack }}</span>
        <span
          v-if="project"
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
    </div>

    <!-- 页面级的全局控件（比如概览页的时间范围）放这儿。日志页控件多，
         自成一条筛选栏，就不用这个插槽 -->
    <div class="page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.head-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
