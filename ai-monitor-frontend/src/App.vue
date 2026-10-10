<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NMessageProvider, dateZhCN, zhCN } from 'naive-ui'
import AppShell from '@/layouts/AppShell.vue'
import { themeOverrides } from '@/theme'

const route = useRoute()
const isLoginPage = computed(() => route.name === 'login')
</script>

<template>
  <NConfigProvider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider>
      <RouterView v-if="isLoginPage" />
      <AppShell v-else>
        <RouterView />
      </AppShell>
    </NMessageProvider>
  </NConfigProvider>
</template>
