// getRandomValues is also available on the HTTP origins used by local NAS deployments.
export function newTaskKey(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
}
