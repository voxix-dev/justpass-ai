import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Link } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RequireAuth, Shell } from '@/components/Shell'
import Jobs from '@/routes/Jobs'
import Landing from '@/routes/Landing'
import NewJob from '@/routes/NewJob'
import Result from '@/routes/Result'
import SignIn from '@/routes/SignIn'
import './index.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1 } } })

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { index: true, element: <Landing /> },
      { path: 'signin', element: <SignIn /> },
      {
        element: <RequireAuth />,
        children: [
          { path: 'new', element: <NewJob /> },
          { path: 'jobs', element: <Jobs /> },
          { path: 'jobs/:id', element: <Result /> },
        ],
      },
      {
        path: '*',
        element: (
          <div>
            <h1 className="text-3xl">Page not found</h1>
            <Link to="/" className="mt-4 inline-block font-semibold text-ink underline underline-offset-4">
              Go to the home page
            </Link>
          </div>
        ),
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
