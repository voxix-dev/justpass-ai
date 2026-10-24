import createClient from 'openapi-fetch'
import { getIdToken } from '@/auth/auth'
import type { paths } from './schema.gen'

// Same-origin: Firebase Hosting rewrites /api/** to Cloud Run; Vite proxies it in dev.
export const api = createClient<paths>({ baseUrl: '/api' })

api.use({
  /** Add the current bearer token when signed in, preserving other request headers. */
  async onRequest({ request }) {
    const token = await getIdToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
})
