import { expect, it } from 'vitest'
import type { Film } from './api'
import { directoryTree, visiblePackages } from './library'
const series = {
  id: 'tv1',
  kind: 'tv',
  path: '/incoming/Show',
  title: 'Show',
  subtitle_status: 'all_media_verified',
  media: [
    { id: 'ep1', season: 0, episode: 1, title: 'Special', subtitle_status: 'skipped_tmdb_chinese' },
    { id: 'ep2', season: 1, episode: 2, title: 'Failed', subtitle_status: 'failed' },
  ],
  assets: [],
} as unknown as Film
const movie = {
  id: 'm1',
  kind: 'movie',
  path: '/incoming/Film',
  title: 'Film',
  subtitle_status: 'external_verified',
  media: [],
  assets: [],
} as unknown as Film
it('filters incomplete episodes, failures, kind and directory without misclassifying Chinese skip', () => {
  expect(visiblePackages([series, movie], '', 'all', 'subtitle')).toEqual([series])
  expect(visiblePackages([series, movie], '', 'all', 'failed')).toEqual([series])
  expect(visiblePackages([series, movie], 'film', 'movie', 'all', '/incoming')).toEqual([movie])
  expect(visiblePackages([series, movie], '', 'all', 'all', '/other')).toEqual([])
})
it('builds a directory tree with season zero and distinct episode keys', () => {
  const folder = directoryTree([series, movie])[0].children![0]
  expect(folder.key).toBe('dir:/incoming')
  const show = folder.children![0]
  expect(show.children![0].title).toBe('Specials')
  expect(show.children![1].children![0].key).toBe('media:tv1:ep2')
})
