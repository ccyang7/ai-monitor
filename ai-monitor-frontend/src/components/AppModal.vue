<script setup lang="ts">
import { NModal } from 'naive-ui'

defineProps<{ title: string; subtitle?: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <!--
    外壳交给 NModal（焦点陷阱、Esc、滚动锁定、遮罩都是它给的），
    .modal 那套样式原样留着 —— NModal 会把 n-modal 类合并到这个根节点上，
    所以阴影写在 theme.ts 的 Modal token 里，不在这里写
  -->
  <NModal
    :show="true"
    :auto-focus="true"
    :trap-focus="true"
    :block-scroll="true"
    @esc="emit('close')"
    @mask-click="emit('close')"
  >
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head">
        <div>
          <h2 class="modal-title">{{ title }}</h2>
          <p v-if="subtitle" class="modal-sub">{{ subtitle }}</p>
        </div>
        <button class="modal-close" type="button" aria-label="关闭" @click="emit('close')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
               stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
      <slot />
    </div>
  </NModal>
</template>
