# Web app

Vite + React + TypeScript single-page app, built to static files and served by Firebase Hosting.

The only interface to the backend is `contracts/openapi.json`. The typed client (`src/api/`) is
generated from it with openapi-typescript + openapi-fetch; don't write API calls by hand. CI
regenerates `src/api/schema.gen.ts` and fails if it differs from the committed file.

## Why a client-side SPA

- Auth is a Firebase ID token sent as `Authorization: Bearer`, with no cookies. The server can't
  render signed-in pages without a cookie, so server rendering would gain nothing.
- A static build keeps the `/api/**` same-origin rewrite to Cloud Run on classic Firebase Hosting,
  so there is no CORS.
- Hosting preview channels give every PR its own URL.
- Static files move easily to GCS + Cloud CDN later.

## Libraries

- React Router (SPA mode) for routing.
- TanStack Query for server state; `refetchInterval` polls running jobs.
- Tailwind + shadcn/ui for styling and components.
- react-markdown + remark-math + rehype-katex render answers; KaTeX loads only on the result page.
- openapi-typescript + openapi-fetch for the typed API client.

## Stubs

- `src/auth/auth.ts` is a dev stub that returns a fake signed-in user.
- `src/api/jobs.ts` is an in-memory fake of the Jobs API. Type "fail" in a question to see the
  error state.

Both can be swapped for the real thing without touching the screens.

Test prep (`/prep` and the "In the lab" cards on the landing page) is not built yet. Every such
feature uses one treatment (`src/components/lab.tsx`): pink hazard tape, an "In the lab" sticker,
and sample UI marked "Sample" behind a hatched frame. Nothing there calls an API. The only control,
"Notify me", is stored in `localStorage` (`src/lib/notify.ts`) until a waitlist endpoint exists.
The "Time saved" number on My jobs is an estimate: 20 minutes per finished job.

## API prefix (open item)

Firebase Hosting forwards the full path, so the API will receive `/api/...`. The dev server proxies
`/api` to `localhost:8000` and strips the prefix, because the API currently serves routes at the
root. The API must handle the `/api` prefix before going live.

## Running

On this team's Fedora Atomic hosts Node tooling runs in the `bloated-jabbascript` toolbox. Elsewhere,
plain `pnpm ...` works.

```
toolbox run -c bloated-jabbascript pnpm --dir apps/web install
toolbox run -c bloated-jabbascript pnpm --dir apps/web dev --host 0.0.0.0
toolbox run -c bloated-jabbascript pnpm --dir apps/web lint        # also: typecheck, build
toolbox run -c bloated-jabbascript pnpm --dir apps/web gen:api     # after contracts/openapi.json changes
```
