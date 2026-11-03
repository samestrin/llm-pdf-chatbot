// The backend is reached at same-origin `/api`; nginx and the Vite dev server
// proxy it (see nginx.conf and vite.config.ts).
export const API_BASE = '/api'

export type Health = 'ok' | 'unreachable'

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal })
    return res.ok ? 'ok' : 'unreachable'
  } catch (err) {
    if (signal?.aborted) throw err
    return 'unreachable'
  }
}
