<script setup lang="ts">
import { useRoute } from 'vue-router'

defineProps<{ projectId: number }>()

const route = useRoute()
</script>

<template>
  <!-- 只放已经做出来的子页：概览、Token、成本这些等对应页面做完了再往这里加，
       摆一个点了没反应的入口比不摆更糟 -->
  <nav class="tabs">
    <RouterLink
      class="tab"
      :class="{ 'is-on': route.name === 'project-logs' }"
      :to="{ name: 'project-logs', params: { projectId } }"
    >
      调用日志
    </RouterLink>
    <RouterLink
      class="tab"
      :class="{ 'is-on': route.name === 'project-settings' }"
      :to="{ name: 'project-settings', params: { projectId } }"
    >
      设置
    </RouterLink>
  </nav>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--gutter);
  overflow-x: auto;
}

.tab {
  flex: none;
  padding: 10px 15px;
  color: var(--muted);
  font-size: var(--fs-base);
  font-weight: 600;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s ease, border-color 0.15s ease;
}

.tab:hover {
  color: var(--green-600);
}

.tab.is-on {
  color: var(--green-700);
  border-bottom-color: var(--green-500);
}
</style>
