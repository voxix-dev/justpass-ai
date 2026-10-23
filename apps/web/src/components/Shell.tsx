import { useEffect, useRef } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { CircleAlert, CircleCheck, ListChecks, LogOut, PenLine, X } from 'lucide-react'
import type { JobStatus } from '@/api/jobs'
import { signOut, useAuth } from '@/auth/auth'
import { Logo } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { isPending, useJobs } from '@/lib/jobs'
import { dismissToast, pushToast, useToasts } from '@/lib/toast'

const NAV = [
  { to: '/new', label: 'New', Icon: PenLine },
  { to: '/jobs', label: 'My jobs', Icon: ListChecks },
]

export function Shell() {
  const user = useAuth()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const main = useRef<HTMLElement>(null)
  const shown = useRef(pathname)
  useJobWatcher(pathname)

  // Move focus to the new page so screen readers announce it, like a full page load would.
  useEffect(() => {
    if (shown.current === pathname) return
    shown.current = pathname
    main.current?.focus({ preventScroll: true })
    window.scrollTo(0, 0)
    dismissToast(pathname.replace('/jobs/', ''))
  }, [pathname])

  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only z-50 rounded-lg bg-primary p-3 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2">
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-1 px-4">
          <Link to="/" className="-ml-2 flex h-11 items-center gap-2 rounded-lg px-2 text-xl font-extrabold tracking-tight text-ink">
            <Logo /> JustPass
          </Link>
          {user && (
            <nav aria-label="Main" className="ml-6 hidden gap-1 sm:flex">
              {NAV.map(({ to, label }) => (
                <NavLink key={to} to={to} end className="group flex h-11 items-center rounded-lg px-3 font-semibold hover:bg-muted">
                  <span className="group-aria-[current=page]:hl group-aria-[current=page]:bg-hl-yellow group-aria-[current=page]:px-1.5">{label}</span>
                </NavLink>
              ))}
            </nav>
          )}
          <div className="ml-auto">
            {user ? (
              <Button variant="ghost" onClick={() => (signOut(), navigate('/'))}>
                <LogOut aria-hidden="true" /> Sign out
              </Button>
            ) : (
              pathname !== '/signin' && (
                <Button asChild variant="outline">
                  <Link to="/signin">Sign in</Link>
                </Button>
              )
            )}
          </div>
        </div>
      </header>

      <main id="main" ref={main} tabIndex={-1} className="mx-auto w-full max-w-5xl flex-1 px-4 pt-6 pb-32 outline-none sm:pt-10 sm:pb-16">
        <Outlet />
      </main>

      {user && (
        <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-2 border-t bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} end className="group flex h-16 flex-col items-center justify-center gap-0.5 text-sm font-bold text-muted-foreground aria-[current=page]:text-ink">
              <Icon className="size-6" aria-hidden="true" />
              <span className="group-aria-[current=page]:hl group-aria-[current=page]:bg-hl-yellow group-aria-[current=page]:px-1.5">{label}</span>
            </NavLink>
          ))}
        </nav>
      )}
      <Toaster />
    </div>
  )
}

export function RequireAuth() {
  const user = useAuth()
  const location = useLocation()
  return user ? <Outlet /> : <Navigate to="/signin" replace state={{ from: location }} />
}

/** Toasts when a job the user saw pending finishes, unless they're already looking at it. */
function useJobWatcher(pathname: string) {
  const { data } = useJobs()
  const seen = useRef(new Map<string, JobStatus>())
  useEffect(() => {
    for (const job of data ?? []) {
      const was = seen.current.get(job.id)
      seen.current.set(job.id, job.status)
      if (!was || was === job.status || isPending(job)) continue
      if (pathname === `/jobs/${job.id}`) continue
      pushToast({ id: job.id, tone: job.status === 'done' ? 'done' : 'failed', title: job.title, to: `/jobs/${job.id}` })
    }
  }, [data, pathname])
}

function Toaster() {
  const toasts = useToasts()
  return (
    <section
      aria-label="Notifications"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-3 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-40 flex flex-col gap-2 sm:inset-x-auto sm:right-6 sm:bottom-6 sm:w-96"
    >
      {toasts.map((t) => {
        const done = t.tone === 'done'
        return (
          <div key={t.id} data-toast={t.id} className="pointer-events-auto flex animate-in items-center gap-3 rounded-xl border-2 border-ink/80 bg-card p-2 pl-3 shadow-[4px_4px_0_var(--ink)] fade-in slide-in-from-bottom-4">
            {done ? (
              <CircleCheck className="size-6 shrink-0 text-ink" aria-hidden="true" />
            ) : (
              <CircleAlert className="size-6 shrink-0 text-destructive" aria-hidden="true" />
            )}
            <Link to={t.to} onClick={() => dismissToast(t.id)} className="flex min-h-11 min-w-0 flex-1 flex-col justify-center rounded-md">
              <span className="font-bold">{done ? 'Your answer is ready' : 'Could not solve this one'}</span>
              <span className="truncate text-sm text-muted-foreground">{t.title}</span>
            </Link>
            <Button variant="ghost" size="icon" aria-label="Dismiss notification" onClick={() => dismissToast(t.id)}>
              <X aria-hidden="true" />
            </Button>
          </div>
        )
      })}
    </section>
  )
}
