import { webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, setCsrf } from './api'
import { newTaskKey } from './taskKey'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setCsrf('')
})

function useHttpCrypto() {
  vi.stubGlobal('isSecureContext', false)
  vi.stubGlobal('crypto', {
    getRandomValues: (bytes: Uint8Array) => webcrypto.getRandomValues(bytes),
  })
}

describe('task keys on HTTP NAS origins', () => {
  it('submits a scan without randomUUID while keeping session and CSRF protection', async () => {
    useHttpCrypto()
    expect(globalThis.isSecureContext).toBe(false)
    expect(globalThis.crypto.randomUUID).toBeUndefined()
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: 'scan-1' })))
    vi.stubGlobal('fetch', fetchMock)
    setCsrf('http-session-csrf')

    await expect(
      api('/scan', {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: newTaskKey() }),
      }),
    ).resolves.toEqual({ task_id: 'scan-1' })

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/scan')
    expect(JSON.parse(options.body).idempotency_key).toMatch(/^[a-f0-9]{32}$/)
    expect(options.credentials).toBe('same-origin')
    expect(options.headers.get('x-csrf-token')).toBe('http-session-csrf')
  })

  it('keeps independent task and batch keys distinct without Math.random', () => {
    useHttpCrypto()
    vi.spyOn(Math, 'random').mockImplementation(() => {
      throw new Error('task keys must use cryptographic randomness')
    })
    const keys = Array.from({ length: 1024 }, () => newTaskKey())
    expect(new Set(keys).size).toBe(keys.length)
    for (const key of keys) {
      expect(key).toMatch(/^[a-zA-Z0-9:_-]{1,100}$/)
      expect(`${key}:999`).toMatch(/^[a-zA-Z0-9:_-]{1,128}$/)
    }
  })

  it('preserves all 16 random bytes, including leading zeroes', () => {
    const bytes = Uint8Array.from([0, 1, 15, 16, 127, 128, 254, 255, 0, 0, 0, 0, 0, 0, 0, 0])
    const fill = vi.fn((target: Uint8Array) => {
      target.set(bytes)
      return target
    })
    vi.stubGlobal('crypto', { getRandomValues: fill })
    expect(newTaskKey()).toBe('00010f107f80feff0000000000000000')
    expect(fill).toHaveBeenCalledOnce()
  })
})
