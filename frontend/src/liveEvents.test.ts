import { afterEach, expect, it, vi } from 'vitest'
import { connectEvents } from './liveEvents'

class Source {
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  handlers: Record<string, (event: unknown) => void> = {}
  closed = false
  addEventListener(name: string, fn: (event: unknown) => void) {
    this.handlers[name] = fn
  }
  close() {
    this.closed = true
  }
  emit(name: string, data: unknown = '', lastEventId = '') {
    this.handlers[name]?.({ data: JSON.stringify(data), lastEventId })
  }
}
afterEach(() => vi.useRealTimers())
it('resumes from last persisted event and refreshes REST after disconnect', () => {
  vi.useFakeTimers()
  const sources: Source[] = [],
    urls: string[] = [],
    changed = vi.fn(),
    state = vi.fn()
  const stop = connectEvents(changed, state, vi.fn(), (url) => {
    urls.push(url)
    const s = new Source()
    sources.push(s)
    return s as unknown as EventSource
  })
  sources[0].onopen!()
  sources[0].emit('reeldock', {}, '12')
  vi.advanceTimersByTime(100)
  expect(changed).toHaveBeenCalledTimes(1)
  sources[0].onerror!()
  sources[0].onerror!()
  vi.advanceTimersByTime(1000)
  expect(sources[0].closed).toBe(true)
  expect(urls).toEqual(['/api/events/stream?after=0', '/api/events/stream?after=12'])
  sources[0].emit('reeldock', {}, '999') // stale connection cannot advance cursor
  sources[1].emit('reset', { cursor: 3 })
  sources[1].onerror!()
  vi.advanceTimersByTime(2000)
  expect(urls[2]).toBe('/api/events/stream?after=3')
  sources[2].emit('auth_expired')
  expect(state).toHaveBeenLastCalledWith('offline')
  const count = urls.length
  vi.runAllTimers()
  expect(urls.length).toBe(count)
  stop()
})
it('survives an unavailable EventSource and cancels reconnect on unmount', () => {
  vi.useFakeTimers()
  const refresh = vi.fn(),
    factory = vi.fn(() => {
      throw Error('offline')
    })
  const stop = connectEvents(refresh, vi.fn(), vi.fn(), factory)
  vi.advanceTimersByTime(100)
  expect(refresh).toHaveBeenCalledTimes(1)
  stop()
  vi.runAllTimers()
  expect(factory).toHaveBeenCalledTimes(1)
})
