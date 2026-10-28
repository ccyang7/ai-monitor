export interface DocItem {
  slug: string
  title: string
}

export interface DocCategory {
  slug: string
  title: string
  items: DocItem[]
}

// 导航手写而不是读 frontmatter：markdown-it 本身不解析 frontmatter，
// 而分组、顺序、是否收录都是产品决策，手写一排最直观
export const DOC_CATEGORIES: DocCategory[] = [
  {
    slug: 'getting-started',
    title: '快速开始',
    items: [
      { slug: 'introduction', title: '平台介绍' },
      { slug: 'quickstart', title: '快速开始' },
      { slug: 'integration', title: '把上报写进代码' },
      { slug: 'verify', title: '接入验收清单' },
    ],
  },
  {
    slug: 'guide',
    title: '使用指南',
    items: [
      { slug: 'concepts', title: '基础概念' },
      { slug: 'home-and-projects', title: '首页与项目' },
      { slug: 'overview', title: '项目概览' },
      { slug: 'logs', title: '调用日志' },
      { slug: 'anomalies', title: '异常分析' },
      { slug: 'models', title: '模型与价格' },
      { slug: 'ai-assistant', title: 'AI 助手' },
      { slug: 'project-settings', title: '项目设置与 Key' },
    ],
  },
  {
    slug: 'api',
    title: 'API 参考',
    items: [
      { slug: 'overview', title: 'API 概览' },
      { slug: 'ingest', title: '上报接口' },
      { slug: 'errors', title: '错误码与处理' },
    ],
  },
  {
    slug: 'faq',
    title: '常见问题',
    items: [
      { slug: 'integration', title: '接入与上报' },
      { slug: 'data-and-cost', title: '数据与成本' },
      { slug: 'product-and-account', title: '功能与账号' },
    ],
  },
]

export const DEFAULT_DOC = {
  category: 'getting-started',
  slug: 'introduction',
}

export const DEFAULT_DOC_PATH = `/docs/${DEFAULT_DOC.category}/${DEFAULT_DOC.slug}`

export interface FlatDocItem extends DocItem {
  category: string
  categoryTitle: string
}

export const DOC_ORDER: FlatDocItem[] = DOC_CATEGORIES.flatMap((category) =>
  category.items.map((item) => ({
    ...item,
    category: category.slug,
    categoryTitle: category.title,
  })),
)

export function findDoc(category: string, slug: string): FlatDocItem | null {
  return DOC_ORDER.find((item) => item.category === category && item.slug === slug) ?? null
}
