<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ apiKey: string }>()

const copied = ref(false)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.apiKey)
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 2000)
  } catch {
    // 剪贴板接口要求安全上下文（https 或 localhost），用局域网 IP 访问会失败。
    // 此时 Key 就在眼前，选不中才怪，不必再弹个错误打断用户
  }
}
</script>

<template>
  <div class="key-reveal">
    <div class="key-reveal-head">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3.5 2.6 20h18.8z" />
        <path d="M12 10v4M12 17.2h.01" />
      </svg>
      这是唯一一次显示完整 Key
    </div>

    <div class="key-reveal-value">
      <code>{{ apiKey }}</code>
      <button class="btn btn-ghost btn-sm" type="button" @click="copy">
        {{ copied ? '已复制' : '复制' }}
      </button>
    </div>

    <p class="key-reveal-hint">
      关掉这个提示后就再也看不到了 —— 服务端只存了它的哈希，我们自己也没法还原。丢了就重新生成一个。
    </p>
  </div>
</template>
