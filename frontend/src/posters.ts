import type { Film } from './api'

export function scrapedPackages(items: Film[]) {
  return items.filter(
    (film) =>
      film.scan_status !== 'superseded' &&
      (film.base_status === 'remote_verified' || film.archive_status === 'archived'),
  )
}

export function posterPackages(
  items: Film[],
  search: string,
  kind: string,
  state: string,
  sort: string,
) {
  const term = search.trim().toLocaleLowerCase()
  return scrapedPackages(items)
    .filter((film) => {
      if (kind !== 'all' && film.kind !== kind) return false
      if (state === 'completed' && !film.scrape_completed) return false
      if (state === 'partial' && film.scrape_completed) return false
      return [
        film.title,
        film.year,
        film.path,
        film.metadata.original_title,
        ...film.metadata.genres,
        ...film.metadata.directors,
        ...film.cast.map((person) => person.name),
      ]
        .join(' ')
        .toLocaleLowerCase()
        .includes(term)
    })
    .sort((a, b) => {
      if (sort === 'year' && a.year !== b.year) return (b.year ?? 0) - (a.year ?? 0)
      if (sort === 'rating' && a.metadata.rating !== b.metadata.rating)
        return (b.metadata.rating ?? -1) - (a.metadata.rating ?? -1)
      return a.title.localeCompare(b.title, 'zh-CN') || a.id.localeCompare(b.id)
    })
}

export function artwork(film: Film, kind: 'poster' | 'fanart') {
  return film.assets.find((asset) => asset.kind === kind && asset.preview)
}
