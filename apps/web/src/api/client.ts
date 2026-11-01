import createClient from 'openapi-fetch'
import { getIdToken } from '@/auth/auth'
import type { paths } from './schema.gen'

const TIMEOUT_MS = 30_000

// Same-origin: Firebase Hosting rewrites /api/** to Cloud Run; Vite proxies it in dev.
export const api = createClient<paths>({
  baseUrl: '/api',
  // Without a limit, a hung server leaves the student on a spinner forever.
  fetch: (request) => fetch(request, { signal: AbortSignal.timeout(TIMEOUT_MS) }),
})

api.use({
  /** Add the current bearer token when signed in, preserving other request headers. */
  async onRequest({ request }) {
    const token = await getIdToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
})
