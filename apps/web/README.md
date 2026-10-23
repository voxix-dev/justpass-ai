# Web app

Vite + React + TypeScript single-page app, deployed as static files to Firebase Hosting.
Requests to `/api/**` are rewritten to Cloud Run on the same origin.

## Development

```
pnpm install
pnpm dev        # http://localhost:5173, proxies /api to localhost:8000
pnpm lint
pnpm typecheck
pnpm build
pnpm gen:api    # regenerate src/api/schema.gen.ts after contracts/openapi.json changes
```

## Stack

- React Router, TanStack Query, Tailwind + shadcn/ui
- react-markdown + remark-math + rehype-katex (KaTeX is loaded only on the result page)
- openapi-typescript + openapi-fetch: the API client is generated from `contracts/openapi.json`.
  CI fails if `src/api/schema.gen.ts` is out of date.

Why a client-side SPA: auth uses a Firebase ID token in the `Authorization` header (no cookies),
so server rendering gains nothing. A static build keeps the same-origin `/api/**` rewrite (no
CORS), gets a preview URL per PR from Hosting, and can move to GCS + Cloud CDN unchanged.

## Stubs

- `src/auth/auth.ts`: fake signed-in user, to be replaced by Firebase Auth.
- `src/api/jobs.ts`: in-memory Jobs API. A question containing "fail" ends in the error state.
- Test prep features are previews only (`src/components/lab.tsx`); "Notify me" is stored in
  `localStorage`.

## Known issues

- The API serves routes at `/`, but Hosting forwards `/api/...`. The dev proxy strips the prefix;
  the API must accept it before deployment.
