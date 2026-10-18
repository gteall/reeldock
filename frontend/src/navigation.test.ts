import { expect, it } from 'vitest'
import { filmLink, readRoute } from './navigation'

it('resolves a direct detail link and the poster wall while preserving existing pages', () => {
  expect(readRoute('#/posters')).toEqual({ page: 'posters' })
  expect(readRoute(filmLink('影片 / #1'))).toEqual({ page: 'posters', filmId: '影片 / #1' })
  expect(readRoute('#/movies')).toEqual({ page: 'movies' })
  expect(readRoute('#/tasks')).toEqual({ page: 'tasks' })
  expect(readRoute('')).toEqual({ page: 'settings' })
})

it('handles malformed and unknown links without crashing', () => {
  expect(readRoute('#/posters/%E0%A4%A')).toEqual({ page: 'posters' })
  expect(readRoute('#/unknown')).toEqual({ page: 'settings' })
  expect(readRoute('#/posters/id/extra')).toEqual({ page: 'settings' })
})
