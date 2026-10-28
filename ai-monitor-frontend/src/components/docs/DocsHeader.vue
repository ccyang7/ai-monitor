<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
</script>

<template>
  <header class="docs-header">
    <RouterLink class="docs-brand" to="/docs">
      <span class="docs-brand-mark">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"
             stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 12h3.5l2.5-6 4 13 2.5-7h7.5" />
        </svg>
      </span>
      <span class="docs-brand-name">AI Monitor</span>
      <span class="docs-brand-divider" />
      <span class="docs-brand-doc">帮助文档</span>
    </RouterLink>

    <div class="docs-header-actions">
      <RouterLink v-if="auth.isLoggedIn" class="docs-header-link" :to="{ name: 'home' }">
        返回应用
      </RouterLink>
      <RouterLink v-else class="docs-header-link" :to="{ name: 'login' }">
        登录 / 注册
      </RouterLink>
    </div>
  </header>
</template>

<style scoped>
.docs-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 32px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
}

.docs-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
}

.docs-brand-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--green-700);
  color: var(--green-100);
}

.docs-brand-mark svg {
  width: 18px;
  height: 18px;
}

.docs-brand-name {
  font-size: var(--fs-md);
  font-weight: 700;
}

.docs-brand-divider {
  width: 1px;
  height: 16px;
  background: var(--line);
}

.docs-brand-doc {
  font-size: var(--fs-sm);
  color: var(--muted);
}

.docs-header-link {
  padding: 7px 14px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: var(--fs-sm);
  color: var(--green-700);
  transition: border-color 0.15s, background 0.15s;
}

.docs-header-link:hover {
  border-color: var(--green-300);
  background: var(--green-50);
}

@media (max-width: 900px) {
  .docs-header {
    padding: 0 16px;
  }

  .docs-brand-divider,
  .docs-brand-doc {
    display: none;
  }
}
</style>
