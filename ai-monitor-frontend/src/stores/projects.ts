import { ref } from 'vue'
import { defineStore } from 'pinia'
import { fetchProjects } from '@/api/projects'
import type { ProjectListItem } from '@/types/project'

// 侧边栏和首页都要这份列表，且侧边栏在项目设置页也要显示 —— 所以它得进 store，
// 否则每次导航都会重新请求一遍
export const useProjectsStore = defineStore('projects', () => {
  const items = ref<ProjectListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const loaded = ref(false)

  async function load(force = false): Promise<void> {
    if (loading.value) return
    if (loaded.value && !force) return

    loading.value = true
    try {
      const data = await fetchProjects()
      items.value = data.items
      total.value = data.total
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  function reset(): void {
    items.value = []
    total.value = 0
    loaded.value = false
  }

  return { items, total, loading, loaded, load, reset }
})
