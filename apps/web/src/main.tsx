import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Link } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ErrorState } from '@/components/bits'
import { RequireAuth, Shell } from '@/components/Shell'
import Jobs from '@/routes/Jobs'
import Landing from '@/routes/Landing'
import NewJob from '@/routes/NewJob'
import Prep from '@/routes/Prep'
import Result from '@/routes/Result'
import SignIn from '@/routes/SignIn'
import './index.css'

// networkMode always: fail with a message when offline, rather than wait silently for a connection.
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, networkMode: 'always' }, mutations: { networkMode: 'always' } },
})

// Render errors (e.g. the lazy Answer chunk failing to load) show this instead of the router's
// default screen. The inner one keeps the shell, so navigation and sign-out stay usable.
const crash = <ErrorState title="This page did not load." onRetry={() => window.location.reload()} />

const router = createBrowserRouter([
  {
    element: <Shell />,
    errorElement: crash,
    children: [
      {
        errorElement: crash,
        children: [
          { index: true, element: <Landing /> },
          { path: 'signin', element: <SignIn /> },
          {
            element: <RequireAuth />,
            children: [
              { path: 'new', element: <NewJob /> },
              { path: 'jobs', element: <Jobs /> },
              { path: 'jobs/:id', element: <Result /> },
              { path: 'prep', element: <Prep /> },
            ],
          },
          {
            path: '*',
            element: (
              <div className="max-w-xl">
                <span className="sticker bg-coral text-ink">404</span>
                <h1 className="mt-5 text-6xl sm:text-7xl">Nothing here. Not even homework.</h1>
                <Link to="/" className="mt-6 inline-flex h-11 items-center font-bold text-volt underline underline-offset-4">
                  Go to the home page
                </Link>
              </div>
            ),
          },
        ],
      },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
