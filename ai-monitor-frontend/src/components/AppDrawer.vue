<script setup lang="ts">
import { NDrawer } from 'naive-ui'

defineProps<{ title: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <!--
    分工同 AppModal：焦点陷阱、Esc、遮罩点击、背景滚动锁定都由 naive 提供，
    抽屉内部的排版（标题栏 + 滚动区）还是手写样式。
    滚动交给 naive 的那层容器，标题栏用 sticky 钉在它顶部 —— 自己再套一层 overflow
    会变成两个滚动容器互相抢滚动条
  -->
  <NDrawer
    :show="true"
    placement="right"
    :width="560"
    :auto-focus="true"
    :trap-focus="true"
    :block-scroll="true"
    @esc="emit('close')"
    @mask-click="emit('close')"
  >
    <div class="drawer" role="dialog" aria-modal="true">
      <div class="drawer-head">
        <h2 class="drawer-title">{{ title }}</h2>
        <button class="drawer-close" type="button" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
               stroke-linecap="round">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div class="drawer-body">
        <slot />
      </div>
    </div>
  </NDrawer>
</template>

<style scoped>
.drawer-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 19px 22px;
  background: var(--card);
  border-bottom: 1px solid var(--line);
}

.drawer-title {
  font-size: var(--fs-md);
  font-weight: 650;
}

.drawer-close {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.drawer-close:hover {
  background: var(--green-50);
  color: var(--green-600);
}

.drawer-close svg {
  width: 17px;
  height: 17px;
}

.drawer-body {
  padding: 22px;
}
</style>
