<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DocsHeader from '@/components/docs/DocsHeader.vue'
import DocsSidebar from '@/components/docs/DocsSidebar.vue'
import DocsToc from '@/components/docs/DocsToc.vue'
import { loadDocSource } from '@/docs/content'
import { renderDoc } from '@/docs/markdown'
import { DOC_CATEGORIES, findDoc } from '@/docs/nav'
import '@/styles/doc-markdown.css'

const DEFAULT_TITLE = 'AI Monitor · 大模型使用监控'

const route = useRoute()
const router = useRouter()

const bodyRef = ref<HTMLElement | null>(null)
const activeId = ref('')

const current = computed(() =>
  findDoc(String(route.params.category ?? ''), String(route.params.slug ?? '')),
)

const rendered = computed(() => {
  if (!current.value) return null
  const source = loadDocSource(current.value.category, current.value.slug)
  return source ? renderDoc(source) : null
})

const selectedPath = computed(() =>
  current.value ? `${current.value.category}/${current.value.slug}` : '',
)

function updateActive(): void {
  const container = bodyRef.value
  if (!container) return

  const headings = container.querySelectorAll<HTMLElement>('h2[id], h3[id]')
  if (!headings.length) return

  // 滚到底时页面无法再让最后一个标题越过 88px 线，放宽阈值到视口 60%，
  // 否则点完目录最后几项，高亮会一直停在前一节
  const nearBottom =
    window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4
  // 锚点靠 scroll-margin-top 停在 88，浏览器会落在 88.0x，严格比 88 会漏掉当前项
  const threshold = nearBottom ? window.innerHeight * 0.6 : 90

  // 当前章节 = 最后一个越过阈值的标题。遍历几十个标题的开销可以忽略
  let currentId = headings[0].id
  for (const heading of headings) {
    if (heading.getBoundingClientRect().top <= threshold) {
      currentId = heading.id
    } else {
      break
    }
  }
  activeId.value = currentId
}

let rafId = 0
function onScroll(): void {
  if (rafId !== 0) return
  rafId = requestAnimationFrame(() => {
    rafId = 0
    updateActive()
  })
}

async function scrollToRouteHash(): Promise<void> {
  if (!route.hash) return
  let id = route.hash.slice(1)
  try {
    id = decodeURIComponent(id)
  } catch {
    // hash 不是合法编码时按原样查
  }
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
}

watch(
  current,
  async (doc) => {
    document.title = doc ? `${doc.title} · 帮助文档 · AI Monitor` : '帮助文档 · AI Monitor'
    activeId.value = rendered.value?.headings[0]?.id ?? ''
    await nextTick()
    updateActive()
    // 首次打开带 hash 的深链时，组件晚于路由的 scrollBehavior 渲染，这里补一次
    await scrollToRouteHash()
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('scroll', onScroll)
  if (rafId !== 0) cancelAnimationFrame(rafId)
  document.title = DEFAULT_TITLE
})

function onMobileSelect(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  const [category, slug] = value.split('/')
  if (category && slug) {
    void router.push({ name: 'docs', params: { category, slug } })
  }
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // 权限被拒或非安全上下文，继续走下面的降级
    }
  }

  // 部署是 HTTP 明文，navigator.clipboard 在非安全上下文里根本不存在
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
}

async function onContentClick(event: MouseEvent): Promise<void> {
  const target = event.target as HTMLElement | null
  if (!target) return

  const copyButton = target.closest<HTMLElement>('.code-copy')
  if (copyButton) {
    const code = copyButton.closest('.code-block')?.querySelector('code')
    if (!code) return
    await copyText(code.textContent ?? '')
    copyButton.textContent = '已复制'
    window.setTimeout(() => {
      copyButton.textContent = '复制'
    }, 1500)
    return
  }

  const anchor = target.closest('a')
  const href = anchor?.getAttribute('href')
  if (!href) return

  if (href.startsWith('#')) {
    event.preventDefault()
    void router.push({ hash: href })
    return
  }

  // 站内链接若是整页刷新，文档站的体验就断了
  if (href.startsWith('/')) {
    event.preventDefault()
    void router.push(href)
  }
}
</script>

<template>
  <div class="docs-page">
    <DocsHeader />

    <div class="docs-layout">
      <DocsSidebar />

      <main class="docs-main">
        <select
          class="docs-mobile-select"
          aria-label="选择文档"
          :value="selectedPath"
          @change="onMobileSelect"
        >
          <optgroup
            v-for="category in DOC_CATEGORIES"
            :key="category.slug"
            :label="category.title"
          >
            <option
              v-for="item in category.items"
              :key="item.slug"
              :value="`${category.slug}/${item.slug}`"
            >
              {{ item.title }}
            </option>
          </optgroup>
        </select>

        <article
          v-if="rendered"
          ref="bodyRef"
          class="doc-body"
          v-html="rendered.html"
          @click="onContentClick"
        />
        <article v-else class="doc-body">
          <h1>没有找到这篇文档</h1>
          <p>
            链接可能已经失效，请从左侧目录重新选择，或返回
            <RouterLink to="/docs">文档首页</RouterLink>。
          </p>
        </article>
      </main>

      <DocsToc v-if="rendered" :headings="rendered.headings" :active-id="activeId" />
    </div>
  </div>
</template>

<style scoped>
.docs-page {
  min-height: 100vh;
  background: var(--card);
  color: var(--text);
}

.docs-layout {
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr) 220px;
  gap: 48px;
  max-width: 1440px;
  margin: 0 auto;
  padding: 0 32px;
}

.docs-main {
  min-width: 0;
  padding: 40px 0 96px;
}

.doc-body {
  max-width: 780px;
  margin: 0 auto;
}

.docs-mobile-select {
  display: none;
  width: 100%;
  margin-bottom: 24px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--card);
  color: var(--text);
  font: inherit;
  font-size: var(--fs-sm);
}

@media (max-width: 1280px) {
  .docs-layout {
    grid-template-columns: 240px minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .docs-layout {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    padding: 0 20px;
  }

  .docs-main {
    padding-top: 24px;
  }

  .docs-mobile-select {
    display: block;
  }
}
</style>
