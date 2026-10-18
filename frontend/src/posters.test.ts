import { expect, it } from 'vitest'
import type { Film } from './api'
import { artwork, posterPackages, scrapedPackages } from './posters'

const film = (id: string, changes: Partial<Film> = {}): Film =>
  ({
    id,
    kind: 'movie',
    title: id,
    year: 2020,
    path: `/library/${id}`,
    base_status: 'remote_verified',
    archive_status: 'not_started',
    scan_status: 'stable',
    cast: [],
    assets: [],
    metadata: {
      original_title: 'Original Title',
      genres: ['剧情'],
      directors: ['Director'],
      rating: null,
    } as Film['metadata'],
    ...changes,
  }) as Film

it('includes scraped and archived packages while excluding partial assets and superseded packages', () => {
  const complete = film('complete')
  const archived = film('archived', { archive_status: 'archived', scan_status: 'archived' })
  const matched = film('matched', { base_status: 'pending', match_status: 'matched' })
  const failed = film('failed', { base_status: 'failed' })
  const old = film('old', { scan_status: 'superseded' })
  expect(scrapedPackages([complete, archived, matched, failed, old])).toEqual([complete, archived])
})

it('combines search, media type and completion filters', () => {
  const movie = film('Film', { scrape_completed: true })
  const series = film('Series', { kind: 'tv', cast: [{ name: '演员甲' }] as Film['cast'] })
  const items = [movie, series]
  expect(posterPackages(items, ' original title ', 'movie', 'completed', 'title')).toEqual([movie])
  expect(posterPackages(items, '演员甲', 'tv', 'partial', 'title')).toEqual([series])
  expect(posterPackages(items, 'DIRECTOR', 'all', 'all', 'title')).toHaveLength(2)
  expect(posterPackages(items, '剧情', 'tv', 'completed', 'title')).toEqual([])
  expect(posterPackages(items, '/library/Film', 'all', 'all', 'title')).toEqual([movie])
})

it('sorts missing years and ratings last, preserves zero ratings and leaves the input unchanged', () => {
  const older = film('older', { year: 1999 })
  const zero = film('zero', { year: null, metadata: { ...older.metadata, rating: 0 } })
  const newer = film('newer', { year: 2025, metadata: { ...older.metadata, rating: 8.2 } })
  const items = [older, zero, newer]
  expect(posterPackages(items, '', 'all', 'all', 'year')).toEqual([newer, older, zero])
  expect(posterPackages(items, '', 'all', 'all', 'rating')).toEqual([newer, zero, older])
  expect(items).toEqual([older, zero, newer])
})

it('uses cached artwork of the correct kind even when the first image has no cache', () => {
  const item = film('art', {
    assets: [
      { kind: 'poster', preview: null },
      { kind: 'season_poster', preview: '/api/assets/season/preview' },
      { kind: 'poster', preview: '/api/assets/poster/preview' },
    ] as Film['assets'],
  })
  expect(artwork(item, 'poster')?.preview).toBe('/api/assets/poster/preview')
  expect(artwork(item, 'fanart')).toBeUndefined()
})
