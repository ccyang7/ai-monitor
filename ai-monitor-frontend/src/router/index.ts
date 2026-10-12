import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
  },
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
  },
  {
    // 项目下的子页，靠 ProjectTabs 互相切换。概览是这个项目的落地页，
    // 侧边栏和首页的项目入口都指向它
    path: '/projects/:projectId',
    name: 'project-overview',
    component: () => import('@/views/ProjectOverviewView.vue'),
    props: true,
  },
  {
    path: '/projects/:projectId/logs',
    name: 'project-logs',
    component: () => import('@/views/ProjectLogsView.vue'),
    props: true,
  },
  {
    path: '/projects/:projectId/anomalies',
    name: 'project-anomalies',
    component: () => import('@/views/ProjectAnomaliesView.vue'),
    props: true,
  },
  {
    path: '/projects/:projectId/settings',
    name: 'project-settings',
    component: () => import('@/views/ProjectSettingsView.vue'),
    props: true,
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: { name: 'home' },
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // restore 内部有短路，重复导航不会重复请求 /users/me
  await auth.restore()

  if (to.name === 'login') {
    return auth.isLoggedIn ? { name: 'home' } : true
  }

  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  return true
})
