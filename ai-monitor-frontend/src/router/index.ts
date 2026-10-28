import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { DEFAULT_DOC_PATH } from '@/docs/nav'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { layout: 'bare' },
  },
  {
    path: '/docs',
    redirect: DEFAULT_DOC_PATH,
  },
  {
    path: '/docs/:category/:slug',
    name: 'docs',
    component: () => import('@/views/DocsView.vue'),
    meta: { layout: 'bare', public: true },
  },
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
  },
  {
    // 模型管理挂在账号层而不是项目层：内置模型人人可见，自建模型属于账号而非某个项目
    path: '/models',
    name: 'models',
    component: () => import('@/views/ModelsView.vue'),
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

function findHashElement(hash: string): HTMLElement | null {
  try {
    return document.getElementById(decodeURIComponent(hash.slice(1)))
  } catch {
    return document.getElementById(hash.slice(1))
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition

    if (to.name === 'docs') {
      // 用 Element 而不是 CSS 选择器：中文 id 直接进 querySelector 需要转义
      if (to.hash) {
        const el = findHashElement(to.hash)
        // 88 = 顶栏 64px + 24px 呼吸空间，必须与 doc-markdown.css 的 scroll-margin-top 保持一致
        if (el) return { el, top: 88, behavior: 'smooth' }
      }
      return { top: 0 }
    }

    // 从文档滚到一半切回应用时，别把文档的滚动位置带过去
    if (from.name === 'docs') return { top: 0 }
    return undefined
  },
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (to.name === 'login') {
    await auth.restore()
    return auth.isLoggedIn ? { name: 'home' } : true
  }

  // 公开页不调 restore：localStorage 里的过期 token 会触发一次必然 401 的 /users/me，
  // 拦截器会因此把正在看文档的人踢去登录页
  if (to.meta.public === true) {
    return true
  }

  // restore 内部有短路，重复导航不会重复请求 /users/me
  await auth.restore()

  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  return true
})
