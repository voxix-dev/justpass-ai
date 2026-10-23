import { useEffect, useRef } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { CircleAlert, CircleCheck, FlaskConical, ListChecks, LogOut, PenLine, X } from 'lucide-react'
import type { JobStatus } from '@/api/jobs'
import { signOut, useAuth } from '@/auth/auth'
import { Logo, Soon } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { isPending, useJobs } from '@/lib/jobs'
import { dismissToast, pushToast, useToasts } from '@/lib/toast'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/new', label: 'New', Icon: PenLine, soon: false },
  { to: '/jobs', label: 'My jobs', Icon: ListChecks, soon: false },
  { to: '/prep', label: 'Test prep', Icon: FlaskConical, soon: true },
]

export function Shell() {
  const user = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
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
      <header className="sticky top-0 z-30 border-b-2 border-border bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-1 px-4">
          <Link to="/" className="-ml-2 flex h-11 items-center gap-2.5 rounded-lg px-2 font-display text-2xl font-extrabold tracking-tight [font-stretch:85%]">
            <Logo /> JustPass
          </Link>
          {user && (
            <nav aria-label="Main" className="ml-8 hidden gap-1 sm:flex">
              {NAV.map(({ to, label, soon }) => (
                <NavLink key={to} to={to} className="flex h-11 items-center gap-2 rounded-xl px-3 font-bold text-muted-foreground hover:text-foreground aria-[current=page]:bg-raised aria-[current=page]:text-volt">
                  {label}
                  {soon && <>{' '}<Soon /></>}
                </NavLink>
              ))}
            </nav>
          )}
          <div className="ml-auto">
            {user ? (
              <Button variant="ghost" onClick={() => (signOut(), queryClient.clear(), navigate('/'))}>
                <LogOut aria-hidden="true" /> Sign out
              </Button>
            ) : (
              pathname !== '/signin' && (
                <Button asChild>
                  <Link to="/signin">Sign in</Link>
                </Button>
              )
            )}
          </div>
        </div>
      </header>

      <main id="main" ref={main} tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 pt-8 pb-32 outline-none sm:pt-12 sm:pb-20">
        <Outlet />
      </main>

      {user && (
        <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t-2 border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur sm:hidden">
          {NAV.map(({ to, label, Icon, soon }) => (
            <NavLink key={to} to={to} className="group relative flex h-16 flex-col items-center justify-center gap-0.5 text-sm font-bold text-muted-foreground aria-[current=page]:text-foreground">
              <span className="grid h-7 w-12 place-items-center rounded-full group-aria-[current=page]:bg-volt group-aria-[current=page]:text-ink">
                <Icon className="size-5" aria-hidden="true" />
              </span>
              {label}
              {soon && ' '}
              {soon && <Soon className="absolute top-1 right-[calc(50%-2.6rem)] text-[0.7rem] leading-4" />}
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
          <div key={t.id} data-toast={t.id} className={cn('pointer-events-auto flex animate-in items-center gap-3 rounded-2xl border-2 bg-card p-2 pl-3 shadow-[5px_5px_0_#000] fade-in slide-in-from-bottom-4', done ? 'border-volt' : 'border-coral')}>
            {done ? (
              <CircleCheck className="size-7 shrink-0 text-volt" aria-hidden="true" />
            ) : (
              <CircleAlert className="size-7 shrink-0 text-coral" aria-hidden="true" />
            )}
            <Link to={t.to} onClick={() => dismissToast(t.id)} className="flex min-h-11 min-w-0 flex-1 flex-col justify-center rounded-md">
              <span className="font-display text-lg font-extrabold">{done ? 'Your answer is ready' : 'Could not solve this one'}</span>
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
