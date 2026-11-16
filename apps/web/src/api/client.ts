import createClient from 'openapi-fetch'
import { getIdToken } from '@/auth/auth'
import type { paths } from './schema.gen'

const TIMEOUT_MS = 30_000

// Without a limit, a hung server leaves the student on a spinner forever.
const send = (request: Request) => fetch(request, { signal: AbortSignal.timeout(TIMEOUT_MS) })

// Same-origin: Firebase Hosting rewrites /api/** to Cloud Run; Vite proxies it in dev.
export const api = createClient<paths>({
  baseUrl: '/api',
  fetch: async (request) => {
    const retry = request.clone()
    const response = await send(request)
    if (response.status !== 401 || !request.headers.has('Authorization')) return response
    // The token may have been revoked or expired mid-request: retry once with a fresh one.
    const token = await getIdToken(true)
    if (!token) return response
    retry.headers.set('Authorization', `Bearer ${token}`)
    return send(retry)
  },
})

api.use({
  /** Add the current bearer token when signed in, preserving other request headers. */
  async onRequest({ request }) {
    const token = await getIdToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
})
