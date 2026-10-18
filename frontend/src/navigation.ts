const pages = ['posters', 'movies', 'settings', 'tasks', 'events', 'about'] as const
export type Page = (typeof pages)[number]
export interface Route {
  page: Page
  filmId?: string
}

export function readRoute(hash = window.location.hash): Route {
  const [page, id, extra] = hash.replace(/^#\/?/, '').split('/')
  if (!pages.includes(page as Page) || extra !== undefined) return { page: 'settings' }
  if (page === 'posters' && id) {
    try {
      return { page, filmId: decodeURIComponent(id) }
    } catch {
      return { page }
    }
  }
  return { page: page as Page }
}

export function filmLink(id: string) {
  return `#/posters/${encodeURIComponent(id)}`
}
