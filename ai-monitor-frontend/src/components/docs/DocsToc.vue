<script setup lang="ts">
import type { DocHeading } from '@/docs/markdown'

defineProps<{
  headings: DocHeading[]
  activeId: string
}>()
</script>

<template>
  <aside class="docs-toc">
    <div class="docs-toc-title">本页目录</div>
    <nav class="docs-toc-nav">
      <RouterLink
        v-for="heading in headings"
        :key="heading.id"
        class="docs-toc-link"
        :class="{
          'is-level-3': heading.level === 3,
          'is-active': heading.id === activeId,
        }"
        :aria-current="heading.id === activeId ? 'location' : undefined"
        :to="{ hash: '#' + heading.id }"
      >
        {{ heading.text }}
      </RouterLink>
    </nav>
  </aside>
</template>

<style scoped>
.docs-toc {
  position: sticky;
  top: 64px;
  align-self: start;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  padding: 40px 0 48px;
  scrollbar-width: thin;
}

.docs-toc-title {
  margin-bottom: 10px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--faint);
}

.docs-toc-nav {
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--line);
}

.docs-toc-link {
  padding: 4px 0 4px 12px;
  border-left: 2px solid transparent;
  margin-left: -1px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--muted);
  transition: color 0.15s, border-color 0.15s;
}

.docs-toc-link:hover {
  color: var(--ink);
}

.docs-toc-link.is-level-3 {
  padding-left: 26px;
  font-size: 12px;
}

.docs-toc-link.is-active {
  border-left-color: var(--green-500);
  color: var(--green-700);
  font-weight: 600;
}

@media (max-width: 1280px) {
  .docs-toc {
    display: none;
  }
}
</style>
