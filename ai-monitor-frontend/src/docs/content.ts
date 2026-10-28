const sources = import.meta.glob('./*/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

export function loadDocSource(category: string, slug: string): string | null {
  return sources[`./${category}/${slug}.md`] ?? null
}
