# Web app

Vite + React + TypeScript single-page app, deployed as static files to Firebase Hosting.
Requests to `/api/**` are rewritten to Cloud Run on the same origin.

## Development

```
pnpm install
pnpm dev        # http://localhost:5173, proxies /api/** to the API on localhost:8000
pnpm lint
pnpm typecheck
pnpm build
pnpm gen:api    # regenerate src/api/schema.gen.ts after contracts/openapi.json changes
pnpm e2e        # Playwright tests; needs the API on localhost:8000 (see Stubs)
```

Before the first `pnpm e2e`, run `pnpm exec playwright install chromium`.

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
- Jobs come from the real Jobs API, so `pnpm dev` needs the API on localhost:8000. From the repo
  root: `uv run --env-file .env uvicorn api.main:app --reload`, or `podman compose up -d --build api`.
  The API's solver is still a fake that answers at once; a task with the word "fail" fails.
  Photos are not supported yet: a task is text only.
- Test prep features are previews only (`src/components/lab.tsx`); "Notify me" is saved in
  `localStorage` only.
