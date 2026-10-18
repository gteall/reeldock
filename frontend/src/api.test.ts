import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, setCsrf } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
  setCsrf('')
})

describe('authenticated API transport', () => {
  it('sends the session cookie and CSRF token for configuration mutations', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 2 })))
    vi.stubGlobal('fetch', fetchMock)
    setCsrf('session-csrf')
    await api('/config', { method: 'PUT', body: '{}' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/config')
    expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin')
    expect(fetchMock.mock.calls[0][1].headers.get('x-csrf-token')).toBe('session-csrf')
  })
  it('shows safe field errors for invalid configuration', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: 'invalid_request',
            errors: [{ message: '待刮削与已刮削目录必须分离' }],
          }),
          { status: 422 },
        ),
      ),
    )
    await expect(api('/config')).rejects.toThrow('待刮削与已刮削目录必须分离')
  })
  it('retains 401 for the application session reset', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{"detail":"login_required"}', { status: 401 })),
    )
    await expect(api('/tasks')).rejects.toMatchObject({ status: 401, code: 'login_required' })
  })
  it('does not display network exception details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('private signed URL')))
    await expect(api('/tasks')).rejects.toMatchObject({
      status: 0,
      code: 'network_error',
      message: '无法连接 ReelDock 服务',
    })
  })
})
