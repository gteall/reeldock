import type { Film } from './api'

export const subtitlePassed = new Set([
  'skipped_tmdb_chinese',
  'default_audio_chinese',
  'embedded_zh_hans',
  'external_verified',
  'downloaded_verified',
  'all_media_verified',
])
export function visiblePackages(
  items: Film[],
  search: string,
  kind: string,
  state: string,
  directory = '',
) {
  return items.filter((item) => {
    if (!`${item.title} ${item.path}`.toLowerCase().includes(search.toLowerCase())) return false
    if (kind !== 'all' && item.kind !== kind) return false
    if (directory && item.path !== directory && !item.path.startsWith(directory + '/')) return false
    if (state === 'failed')
      return (
        !!item.error_code ||
        ['failed', 'blocked'].includes(item.task_status ?? '') ||
        item.assets.some((a) => a.status === 'failed') ||
        item.media.some(
          (m) => ['failed', 'needs_review'].includes(m.subtitle_status) || !!m.error_code,
        )
      )
    if (state === 'subtitle')
      return item.media.length
        ? item.media.some((m) => !subtitlePassed.has(m.subtitle_status))
        : !subtitlePassed.has(item.subtitle_status)
    return true
  })
}
export interface LibraryNode {
  key: string
  title: string
  children?: LibraryNode[]
}
export function directoryTree(items: Film[]): LibraryNode[] {
  const root: LibraryNode = { key: 'dir:', title: '全部媒体', children: [] }
  for (const item of items) {
    const parts = item.path.split('/').filter(Boolean)
    let parent = root,
      path = ''
    for (const part of parts.slice(0, -1)) {
      path += '/' + part
      let node = parent.children!.find((n) => n.key === 'dir:' + path)
      if (!node) {
        node = { key: 'dir:' + path, title: part, children: [] }
        parent.children!.push(node)
      }
      parent = node
    }
    const node: LibraryNode = {
      key: 'package:' + item.id,
      title: `${item.kind === 'tv' ? '剧' : '影'} · ${item.title}`,
      children: [],
    }
    for (const number of [
      ...new Set(item.media.map((m) => m.season).filter((n): n is number => n !== null)),
    ].sort((a, b) => a - b)) {
      node.children!.push({
        key: `season:${item.id}:${number}`,
        title: number === 0 ? 'Specials' : `第 ${number} 季`,
        children: item.media
          .filter((m) => m.season === number)
          .map((m) => ({
            key: `media:${item.id}:${m.id}`,
            title: `E${m.episode ?? '?'} · ${m.title}`,
          })),
      })
    }
    if (item.kind === 'tv' && item.media.some((m) => m.season === null)) {
      node.children!.push({
        key: `season:${item.id}:unknown`,
        title: '编号待确认',
        children: item.media
          .filter((m) => m.season === null)
          .map((m) => ({ key: `media:${item.id}:${m.id}`, title: m.title })),
      })
    }
    parent.children!.push(node)
  }
  return [root]
}
