<script setup lang="ts">
import { useRoute } from 'vue-router'
import { DOC_CATEGORIES } from '@/docs/nav'

const route = useRoute()
</script>

<template>
  <aside class="docs-sidebar">
    <nav>
      <div v-for="category in DOC_CATEGORIES" :key="category.slug" class="docs-nav-group">
        <div class="docs-nav-title">{{ category.title }}</div>
        <RouterLink
          v-for="item in category.items"
          :key="item.slug"
          class="docs-nav-link"
          :class="{
            'is-active':
              route.params.category === category.slug && route.params.slug === item.slug,
          }"
          :to="{ name: 'docs', params: { category: category.slug, slug: item.slug } }"
        >
          {{ item.title }}
        </RouterLink>
      </div>
    </nav>
  </aside>
</template>

<style scoped>
.docs-sidebar {
  position: sticky;
  top: 64px;
  align-self: start;
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  padding: 32px 4px 48px 0;
  scrollbar-width: thin;
}

.docs-nav-group + .docs-nav-group {
  margin-top: 26px;
}

.docs-nav-title {
  margin-bottom: 8px;
  padding: 0 10px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--faint);
}

.docs-nav-link {
  display: block;
  padding: 6px 10px;
  border-left: 2px solid transparent;
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  font-size: var(--fs-sm);
  color: var(--muted);
  transition: color 0.15s, background 0.15s;
}

.docs-nav-link:hover {
  background: var(--paper);
  color: var(--ink);
}

.docs-nav-link.is-active {
  border-left-color: var(--green-500);
  background: var(--green-50);
  color: var(--green-700);
  font-weight: 600;
}

@media (max-width: 900px) {
  .docs-sidebar {
    display: none;
  }
}
</style>
