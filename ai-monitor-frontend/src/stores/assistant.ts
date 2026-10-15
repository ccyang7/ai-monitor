import { ref } from 'vue'
import { defineStore } from 'pinia'

// 悬浮抽屉平时只由右下角那个浮球打开。页面上的入口（模型管理的「问 AI 价格」）
// 够不着它，就往前这里放一条待发问题，抽屉 watcher 到就自己打开并发送
export const useAssistantStore = defineStore('assistant', () => {
  const pending = ref<string | null>(null)

  function ask(question: string): void {
    pending.value = question
  }

  // 取走即清空：抽屉消费完值回到 null，再点一次同一条链接才会重新触发
  function consume(): string | null {
    const question = pending.value
    pending.value = null
    return question
  }

  return { pending, ask, consume }
})
