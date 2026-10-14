<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { streamChat } from '@/api/chat'
import { useProjectsStore } from '@/stores/projects'
import type { ChatEvent, ChatMessage } from '@/types/chat'

const THREAD_KEY = 'ai-monitor:chat-thread'
const WIDE_KEY = 'ai-monitor:chat-wide'

// html:false 是安全边界，不是风格选择 —— 模型输出不可信，允许原始 HTML 就是开了一个注入口
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 助手是浮层，链接跳转会把用户从当前页面带走，一律新窗口开
md.renderer.rules.link_open = (tokens, idx, options, _env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return self.renderToken(tokens, idx, options)
}

const route = useRoute()
const projects = useProjectsStore()

const open = ref(false)
// 宽度存 localStorage 而 thread_id 存 sessionStorage：宽度是人的偏好（换个标签页也该记住），
// 会话是本次浏览的
const wide = ref(localStorage.getItem(WIDE_KEY) === '1')
const messages = ref<ChatMessage[]>([])
const input = ref('')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)
let controller: AbortController | null = null

// 不用 crypto.randomUUID：它要求安全上下文，从局域网 IP 用 http 访问时该 API 直接不存在
function newThreadId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// 存的是前端这半截 uuid，不是后端返回的完整 thread_id —— 前缀由服务端每次现拼，
// 把 user_9_xxx 再传回去会拼成 user_9_user_9_xxx
const stored = sessionStorage.getItem(THREAD_KEY)
const threadId = ref(stored ?? newThreadId())
if (!stored) sessionStorage.setItem(THREAD_KEY, threadId.value)

const currentProject = computed(() => {
  const id = Number(route.params.projectId)
  if (!id) return null
  return projects.items.find((item) => item.id === id)?.name ?? null
})

let seq = 0

function makeMessage(role: ChatMessage['role'], partial: Partial<ChatMessage> = {}): ChatMessage {
  seq += 1
  return {
    id: `m${seq}`,
    role,
    content: '',
    status: 'done',
    tool: null,
    error: null,
    ...partial,
  }
}

// 每处都现从数组里取。push 进去的原始对象绕过了 Proxy，直接改它的属性界面不会更新 ——
// Vue 3 的响应式是惰性代理，只有读写发生在代理上才被追踪
function latest(): ChatMessage | undefined {
  return messages.value[messages.value.length - 1]
}

function scrollToBottom(): void {
  void nextTick(() => {
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function handleEvent(event: ChatEvent): void {
  const reply = latest()
  if (!reply || reply.role !== 'assistant') return

  if (event.type === 'status') {
    reply.tool =
      event.stage === 'tool_start' ? { name: event.name, label: event.label } : null
    return
  }
  if (event.type === 'delta') {
    reply.content += event.text
    scrollToBottom()
    return
  }
  if (event.type === 'done') {
    reply.tool = null
    reply.status = 'done'
    return
  }
  reply.tool = null
  reply.status = 'error'
  reply.error = event.message
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || sending.value) return

  const target = currentProject.value
  messages.value.push(makeMessage('user', { content: text }))
  messages.value.push(makeMessage('assistant', { status: 'streaming' }))
  input.value = ''
  sending.value = true
  scrollToBottom()

  controller = new AbortController()
  try {
    await streamChat(
      { message: text, thread_id: threadId.value, current_project: target },
      handleEvent,
      controller.signal,
    )
    // 流走完了却没等到 done：连接中途断了。不补这个状态的话气泡会永远转圈
    const reply = latest()
    if (reply?.status === 'streaming') {
      reply.status = 'error'
      reply.error = '连接中断，回答可能不完整'
    }
  } catch (error) {
    const reply = latest()
    if (!reply || reply.role !== 'assistant') return
    if (isAbort(error)) {
      reply.status = 'aborted'
    } else {
      reply.status = 'error'
      reply.error = error instanceof Error ? error.message : '发生未知错误'
    }
  } finally {
    const reply = latest()
    if (reply?.role === 'assistant') reply.tool = null
    sending.value = false
    controller = null
  }
}

function stop(): void {
  controller?.abort()
}

function newChat(): void {
  messages.value = []
  threadId.value = newThreadId()
  sessionStorage.setItem(THREAD_KEY, threadId.value)
}

function toggleWide(): void {
  wide.value = !wide.value
  localStorage.setItem(WIDE_KEY, wide.value ? '1' : '0')
}

function onEnter(event: KeyboardEvent): void {
  // 中文输入法用 Enter 确认候选词时也会触发 keydown，这时绝不能当发送
  if (event.isComposing || event.shiftKey) return
  event.preventDefault()
  void send()
}
</script>

<template>
  <button v-if="!open" class="ai-fab" type="button" title="AI 助手" @click="open = true">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-2.8-.4L4 21l1.4-4.1A8.2 8.2 0 0 1 3 11.5 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5z" />
    </svg>
  </button>

  <section v-else class="ai-panel" :class="{ 'is-wide': wide }">
    <header class="ai-head">
      <span class="ai-head-dot" />
      <span class="ai-head-title">AI 助手</span>
      <span v-if="currentProject" class="ai-head-scope" :title="currentProject">{{ currentProject }}</span>
      <button class="ai-head-btn" type="button" :disabled="sending" @click="newChat">新对话</button>
      <button
        class="ai-head-btn is-icon"
        type="button"
        :title="wide ? '收窄' : '加宽'"
        @click="toggleWide"
      >
        <svg v-if="wide" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"
             stroke-linecap="round" stroke-linejoin="round">
          <polyline points="4 14 10 14 10 20" />
          <polyline points="20 10 14 10 14 4" />
          <line x1="14" y1="10" x2="21" y2="3" />
          <line x1="3" y1="21" x2="10" y2="14" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"
             stroke-linecap="round" stroke-linejoin="round">
          <polyline points="15 3 21 3 21 9" />
          <polyline points="9 21 3 21 3 15" />
          <line x1="21" y1="3" x2="14" y2="10" />
          <line x1="3" y1="21" x2="10" y2="14" />
        </svg>
      </button>
      <button class="ai-head-btn is-icon" type="button" title="收起" @click="open = false">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </header>

    <div ref="listEl" class="ai-list">
      <p v-if="!messages.length" class="ai-empty">
        问我关于你 AI 项目的数据，比如「最近 7 天哪个项目用得最多？」
      </p>

      <div v-for="msg in messages" :key="msg.id" class="ai-msg" :class="`is-${msg.role}`">
        <div v-if="msg.tool" class="ai-tool">
          <span class="ai-tool-dot" />正在{{ msg.tool.label }}…
        </div>
        <div
          v-if="msg.content"
          class="ai-bubble"
          v-html="md.render(msg.content)"
        />
        <div v-else-if="msg.status === 'streaming' && !msg.tool" class="ai-bubble is-waiting">
          <span class="ai-typing"><i /><i /><i /></span>
        </div>
        <p v-if="msg.error" class="ai-error">{{ msg.error }}</p>
        <p v-if="msg.status === 'aborted'" class="ai-note">已停止生成</p>
      </div>
    </div>

    <footer class="ai-foot">
      <textarea
        v-model="input"
        class="ai-input"
        rows="1"
        placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
        @keydown.enter="onEnter"
      />
      <button v-if="sending" class="ai-send is-stop" type="button" @click="stop">停止</button>
      <button v-else class="ai-send" type="button" :disabled="!input.trim()" @click="send">发送</button>
    </footer>
  </section>
</template>

<style scoped>
.ai-fab {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 90;
  width: 52px;
  height: 52px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: linear-gradient(140deg, var(--green-500), var(--green-700));
  color: #fff;
  cursor: pointer;
  box-shadow: 0 6px 22px rgba(11, 35, 28, 0.28);
  transition: transform 0.15s, box-shadow 0.15s;
}

.ai-fab:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 28px rgba(11, 35, 28, 0.34);
}

.ai-fab svg {
  width: 24px;
  height: 24px;
}

.ai-panel {
  position: fixed;
  right: 26px;
  bottom: 26px;
  z-index: 91;
  display: flex;
  flex-direction: column;
  width: 400px;
  max-width: calc(100vw - 32px);
  height: 80vh;
  max-height: 660px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  box-shadow: 0 12px 42px rgba(11, 35, 28, 0.24);
  overflow: hidden;
  transition: width 0.18s ease;
}

/* 宽档是给表格用的：窄档下 5 列表格每列都在换行。
   小屏仍被上面的 max-width 兜住 */
.ai-panel.is-wide {
  width: 760px;
}

.ai-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(140deg, var(--green-700), var(--green-800));
  color: #fff;
}

.ai-head-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green-300);
}

.ai-head-title {
  font-size: var(--fs-md);
  font-weight: 600;
}

.ai-head-scope {
  max-width: 130px;
  margin-left: 2px;
  padding: 1px 8px;
  border-radius: var(--r-pill);
  background: rgba(255, 255, 255, 0.16);
  font-size: var(--fs-xs);
  color: var(--green-100);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-head-btn {
  padding: 4px 10px;
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: var(--r-pill);
  background: transparent;
  color: #fff;
  font-family: inherit;
  font-size: var(--fs-xs);
  cursor: pointer;
}

.ai-head-btn:first-of-type {
  margin-left: auto;
}

.ai-head-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.14);
}

.ai-head-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ai-head-btn.is-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
}

.ai-head-btn.is-icon svg {
  width: 15px;
  height: 15px;
}

.ai-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 14px;
  overflow-y: auto;
}

.ai-empty {
  margin: auto;
  padding: 0 24px;
  color: var(--faint);
  font-size: var(--fs-sm);
  text-align: center;
  line-height: 1.8;
}

.ai-msg {
  display: flex;
  flex-direction: column;
  gap: 7px;
  max-width: 88%;
}

.ai-msg.is-user {
  align-self: flex-end;
  align-items: flex-end;
}

.ai-bubble {
  padding: 9px 13px;
  border-radius: 12px;
  font-size: var(--fs-sm);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.is-user .ai-bubble {
  border-bottom-right-radius: 4px;
  background: var(--green-600);
  color: #fff;
  white-space: pre-wrap;
}

.is-assistant .ai-bubble {
  border-bottom-left-radius: 4px;
  background: var(--paper);
  color: var(--text);
}

.is-assistant .ai-bubble :deep(p) {
  margin: 0 0 7px;
}

.is-assistant .ai-bubble :deep(p:last-child) {
  margin-bottom: 0;
}

/* tokens.css 把全局 ul 的 list-style 清掉了，markdown 的列表得显式加回来 */
.is-assistant .ai-bubble :deep(ul),
.is-assistant .ai-bubble :deep(ol) {
  margin: 5px 0;
  padding-left: 20px;
}

.is-assistant .ai-bubble :deep(ul) {
  list-style: disc;
}

.is-assistant .ai-bubble :deep(ol) {
  list-style: decimal;
}

.is-assistant .ai-bubble :deep(li) {
  margin: 2px 0;
}

.is-assistant .ai-bubble :deep(li::marker) {
  color: var(--green-500);
}

/* 模型很爱用表格答「近 7 天各项目用量」这类问题，不给样式就是一堆 1px 内边距的文字挤在一起 */
.is-assistant .ai-bubble :deep(table) {
  display: block;
  max-width: 100%;
  margin: 7px 0;
  border-collapse: collapse;
  overflow-x: auto;
  font-size: var(--fs-xs);
}

.is-assistant .ai-bubble :deep(th),
.is-assistant .ai-bubble :deep(td) {
  padding: 5px 9px;
  border: 1px solid var(--line);
  white-space: nowrap;
}

.is-assistant .ai-bubble :deep(th) {
  background: var(--green-50);
  color: var(--green-700);
  font-weight: 600;
}

.is-assistant .ai-bubble :deep(strong) {
  color: var(--ink);
  font-weight: 600;
}

.is-assistant .ai-bubble :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(11, 35, 28, 0.07);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.is-assistant .ai-bubble :deep(pre) {
  margin: 6px 0;
  padding: 10px 12px;
  border-radius: var(--r-sm);
  background: var(--green-900);
  overflow-x: auto;
}

.is-assistant .ai-bubble :deep(pre code) {
  padding: 0;
  background: none;
  color: var(--green-100);
}

.is-assistant .ai-bubble :deep(a) {
  color: var(--green-600);
  text-decoration: underline;
}

.is-assistant .ai-bubble :deep(h1),
.is-assistant .ai-bubble :deep(h2),
.is-assistant .ai-bubble :deep(h3) {
  margin: 8px 0 5px;
  font-size: var(--fs-base);
}

.ai-bubble.is-waiting {
  display: inline-flex;
  padding: 12px 14px;
}

.ai-typing {
  display: inline-flex;
  gap: 4px;
}

.ai-typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--faint);
  animation: ai-pulse 1.2s ease-in-out infinite;
}

.ai-typing i:nth-child(2) {
  animation-delay: 0.18s;
}

.ai-typing i:nth-child(3) {
  animation-delay: 0.36s;
}

.ai-tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 5px 12px;
  border-radius: var(--r-pill);
  background: var(--green-50);
  color: var(--green-600);
  font-size: var(--fs-xs);
}

.ai-tool-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green-500);
  animation: ai-pulse 1s ease-in-out infinite;
}

.ai-error {
  padding: 7px 11px;
  border-radius: var(--r-sm);
  background: var(--red-bg);
  color: var(--red);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.ai-note {
  color: var(--faint);
  font-size: var(--fs-xs);
}

.ai-foot {
  display: flex;
  gap: 8px;
  padding: 11px 12px;
  border-top: 1px solid var(--line);
  background: var(--card);
}

.ai-input {
  flex: 1;
  min-height: 40px;
  max-height: 120px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--card);
  color: var(--text);
  font-family: inherit;
  font-size: var(--fs-sm);
  line-height: 1.5;
  resize: none;
}

.ai-input:focus {
  border-color: var(--green-500);
  outline: none;
}

.ai-send {
  align-self: flex-end;
  height: 40px;
  padding: 0 18px;
  border: none;
  border-radius: var(--r-md);
  background: var(--green-500);
  color: #fff;
  font-family: inherit;
  font-size: var(--fs-sm);
  cursor: pointer;
}

.ai-send:hover:not(:disabled) {
  background: var(--green-600);
}

.ai-send:disabled {
  background: var(--line);
  color: var(--faint);
  cursor: not-allowed;
}

.ai-send.is-stop {
  background: var(--red-bg);
  color: var(--red);
}

.ai-send.is-stop:hover {
  background: #f6ddd9;
}

@keyframes ai-pulse {
  0%,
  100% {
    opacity: 0.3;
  }
  50% {
    opacity: 1;
  }
}
</style>
