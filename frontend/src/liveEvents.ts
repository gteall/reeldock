export type LiveState = 'connecting' | 'live' | 'reconnecting' | 'offline'
type Factory = (url: string) => EventSource

export function connectEvents(
  onChange: () => void,
  onState: (state: LiveState) => void,
  onAuthExpired: () => void,
  factory: Factory = (url) => new EventSource(url, { withCredentials: true }),
) {
  let source: EventSource | null = null,
    cursor = 0,
    failures = 0,
    closed = false
  let reconnect: ReturnType<typeof setTimeout> | undefined,
    refresh: ReturnType<typeof setTimeout> | undefined
  const changed = () => {
    if (!refresh)
      refresh = setTimeout(() => {
        refresh = undefined
        if (!closed) onChange()
      }, 100)
  }
  const retry = () => {
    if (closed) return
    source?.close()
    source = null
    onState('reconnecting')
    changed() // REST remains the source of truth during a disconnection.
    clearTimeout(reconnect)
    reconnect = setTimeout(open, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)))
  }
  const open = () => {
    if (closed) return
    onState(failures ? 'reconnecting' : 'connecting')
    let current: EventSource
    try {
      current = factory(`/api/events/stream?after=${cursor}`)
    } catch {
      retry()
      return
    }
    source = current
    const active = () => !closed && source === current
    current.onopen = () => {
      if (active()) {
        failures = 0
        onState('live')
        changed()
      }
    }
    current.addEventListener('ready', () => {
      if (active()) changed()
    })
    current.addEventListener('reeldock', (event) => {
      if (!active()) return
      const id = Number((event as MessageEvent).lastEventId)
      if (Number.isSafeInteger(id) && id > cursor) {
        cursor = id
        changed()
      }
    })
    current.addEventListener('reset', (event) => {
      if (!active()) return
      try {
        const value = JSON.parse((event as MessageEvent).data).cursor
        cursor = Number.isSafeInteger(value) && value >= 0 ? value : 0
      } catch {
        cursor = 0
      }
      changed()
    })
    current.addEventListener('auth_expired', () => {
      if (active()) {
        stop()
        onAuthExpired()
      }
    })
    current.onerror = () => {
      if (active()) retry()
    }
  }
  const stop = () => {
    closed = true
    source?.close()
    source = null
    clearTimeout(reconnect)
    clearTimeout(refresh)
    onState('offline')
  }
  open()
  return stop
}
