// 用 undefined 当「不是 JSON」的哨兵，而不是包一层 { ok, value } 返回对象：
// JSON.parse 永远不会产出 undefined，所以哨兵值不会和合法结果撞车
export function tryParseJson(text: string | null | undefined): unknown {
  const trimmed = (text ?? '').trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return undefined
  try {
    return JSON.parse(trimmed)
  } catch {
    return undefined
  }
}
