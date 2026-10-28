import MarkdownIt from 'markdown-it'
import type { Token } from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import http from 'highlight.js/lib/languages/http'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import typescript from 'highlight.js/lib/languages/typescript'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('java', java)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('http', http)

export interface DocHeading {
  level: 2 | 3
  text: string
  id: string
}

export interface RenderedDoc {
  html: string
  headings: DocHeading[]
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  // 文档是长段落，源码里一行一句。开 breaks 会把每个换行变成 <br>，中文段落会碎成一地
  breaks: false,
})

function headingText(inline: Token): string {
  return (inline.children ?? [])
    .filter((child) => child.type === 'text' || child.type === 'code_inline')
    .map((child) => child.content)
    .join('')
}

function slugify(raw: string): string {
  const base = raw
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  return base || 'section'
}

// 标题 id 与右侧目录必须同源：都从这一份 token 里生成，否则中文标题、行内代码、
// 重复标题都会让目录链接和正文锚点对不上
function applyHeadingIds(tokens: Token[], headings: DocHeading[]): void {
  const used = new Map<string, number>()

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]
    if (token.type !== 'heading_open') continue

    const level = token.tag === 'h2' ? 2 : token.tag === 'h3' ? 3 : null
    if (level === null) continue

    const text = headingText(tokens[i + 1])
    if (!text) continue

    const base = slugify(text)
    const count = used.get(base) ?? 0
    used.set(base, count + 1)
    const id = count === 0 ? base : `${base}-${count + 1}`

    token.attrSet('id', id)
    headings.push({ level, text, id })
  }
}

md.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  const lang = (token.info || '').trim().split(/\s+/)[0] || ''
  const highlighted =
    lang && hljs.getLanguage(lang)
      ? hljs.highlight(token.content, { language: lang }).value
      : md.utils.escapeHtml(token.content)
  const label = md.utils.escapeHtml(lang || 'text')

  return (
    `<div class="code-block" data-lang="${label}">` +
    `<div class="code-block-head"><span>${label}</span>` +
    `<button class="code-copy" type="button" aria-label="复制代码">复制</button></div>` +
    `<pre class="hljs"><code>${highlighted}</code></pre></div>`
  )
}

md.renderer.rules.table_open = () => '<div class="table-wrap"><table>\n'
md.renderer.rules.table_close = () => '</table></div>\n'

md.renderer.rules.link_open = (tokens, idx, options, _env, self) => {
  const href = String(tokens[idx].attrGet('href') ?? '')
  if (/^https?:\/\//.test(href)) {
    tokens[idx].attrSet('target', '_blank')
    tokens[idx].attrSet('rel', 'noopener noreferrer')
  }
  return self.renderToken(tokens, idx, options)
}

export function renderDoc(source: string): RenderedDoc {
  const env = {}
  const tokens = md.parse(source, env)
  const headings: DocHeading[] = []
  applyHeadingIds(tokens, headings)
  const html = md.renderer.render(tokens, md.options, env)
  return { html, headings }
}
