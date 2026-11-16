import createClient from 'openapi-fetch'
import { getIdToken } from '@/auth/auth'
import type { paths } from './schema.gen'

const TIMEOUT_MS = 30_000
/** For calls that move a whole file, which can take a while on a slow connection. */
export const FILE_TIMEOUT_MS = 5 * 60_000

/**
 * A fetch that gives up after `timeoutMs` (without a limit, a hung server leaves the student on a
 * spinner forever) and retries once with a fresh token if the first one was refused.
 */
export const fetchWithin = (timeoutMs: number) => async (request: Request) => {
  const send = (r: Request) => fetch(r, { signal: AbortSignal.timeout(timeoutMs) })
  const retry = request.clone()
  const response = await send(request)
  if (response.status !== 401 || !request.headers.has('Authorization')) return response
  // The token may have been revoked or expired mid-request: retry once with a fresh one.
  const token = await getIdToken(true)
  if (!token) return response
  retry.headers.set('Authorization', `Bearer ${token}`)
  return send(retry)
}

// Same-origin: Firebase Hosting rewrites /api/** to Cloud Run; Vite proxies it in dev.
export const api = createClient<paths>({ baseUrl: '/api', fetch: fetchWithin(TIMEOUT_MS) })

api.use({
  /** Add the current bearer token when signed in, preserving other request headers. */
  async onRequest({ request }) {
    const token = await getIdToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
})
