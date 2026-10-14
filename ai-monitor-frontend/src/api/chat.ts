import { tokenStorage } from './client'
import type { ChatEvent, ChatRequest } from '@/types/chat'

const STREAM_URL = '/api/v1/chat/stream'

function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

// 不走 axios：axios 的浏览器适配器要等整个响应体读完才 resolve，拿不到边收边用。
// 也不能用 EventSource —— 它只支持 GET（消息得放请求体里），且不能带 Authorization 头
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(STREAM_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${tokenStorage.read() ?? ''}`,
      },
      body: JSON.stringify(request),
      signal,
    })
  } catch (error) {
    // 用户按停止也是从这里抛出来的，得原样透出去让上层区分「中断」和「故障」
    if (isAbort(error)) throw error
    // fetch 自身的失败是英文 TypeError（Failed to fetch），透出去就是中文界面里蹦英文
    throw new Error('连不上服务，请确认后端已启动')
  }

  if (!response.ok) {
    // 流还没建立起来时后端走的仍是普通状态码（未配 Key 的 503、超长消息的 422），
    // 这时 body 是 JSON 而不是事件流。流一旦开始就没法改状态码了，前面只剩 200
    if (response.status === 401) {
      throw new Error('登录已过期，请刷新页面后重试')
    }
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown
    } | null
    const detail = payload?.detail
    throw new Error(
      typeof detail === 'string' ? detail : `请求失败（HTTP ${response.status}）`,
    )
  }

  const reader = response.body?.getReader()
  if (!reader) {
    throw new Error('当前浏览器不支持流式响应')
  }

  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      // stream: true 让被切在块边界上的多字节中文也能拼回来
      buffer += decoder.decode(value, { stream: true })

      // 按空行分帧。chunk 边界和帧边界毫无关系，半截帧得留在 buffer 里等下一个 chunk ——
      // 这正是不能靠「一次 read 就是一批完整事件」偷懒的原因
      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')

        if (!frame.startsWith('data: ')) continue
        onEvent(JSON.parse(frame.slice('data: '.length)) as ChatEvent)
      }
    }
  } catch (error) {
    if (isAbort(error)) throw error
    // 读到一半后端重启时 read() 抛的同样是英文 TypeError
    throw new Error('连接中断，回答不完整')
  }
}
