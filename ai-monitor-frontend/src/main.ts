import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { setUnauthorizedHandler } from './api/client'
import { router } from './router'
import { useAuthStore } from './stores/auth'
import { useProjectsStore } from './stores/projects'
import './styles/tokens.css'
import './styles/components.css'

const app = createApp(App)

app.use(createPinia())
app.use(router)

// 拦截器只知道 401，不知道用户当时在哪个页面。把跳转交回这里，顺便带上 redirect
setUnauthorizedHandler(() => {
  const current = router.currentRoute.value
  useAuthStore().clear()
  useProjectsStore().reset()

  // 公开页（帮助文档）不该因为一次 401 就被踢去登录
  if (current.name !== 'login' && current.meta.public !== true) {
    void router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

// 要等首次导航（含守卫）跑完再挂载。否则未登录用户会先看到一帧带侧边栏的骨架，
// 而且 AppShell 挂载即请求项目列表，白白打出一个注定 401 的请求
void router.isReady().then(() => app.mount('#app'))
