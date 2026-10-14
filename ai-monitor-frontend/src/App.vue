<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, dateZhCN, zhCN } from 'naive-ui'
import AppShell from '@/layouts/AppShell.vue'
import { themeOverrides } from '@/theme'

const route = useRoute()
const isLoginPage = computed(() => route.name === 'login')

// 切项目时子路由没变（/projects/13/logs → /projects/18/logs），RouterView 会复用同一个
// 组件实例，而四个项目页的数据都写在 onMounted 里 —— 不给 key 就只会一直显示上一个项目的数据
const viewKey = computed(() => String(route.params.projectId ?? ''))
</script>

<template>
  <NConfigProvider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider>
      <RouterView v-if="isLoginPage" />
      <AppShell v-else>
        <RouterView :key="viewKey" />
      </AppShell>
    </NMessageProvider>
  </NConfigProvider>
</template>
