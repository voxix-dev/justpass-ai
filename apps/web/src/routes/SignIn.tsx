import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, type Location } from 'react-router'
import { LoaderCircle } from 'lucide-react'
import {
  completeEmailLink,
  errorCode,
  isEmailLink,
  savedEmail,
  sendEmailLink,
  signInAsGuest,
  useAuthState,
} from '@/auth/auth'
import { Button } from '@/components/ui/button'

type Action = 'email' | 'guest' | 'link'

const MESSAGES: Record<string, string> = {
  'auth/invalid-email': 'That email address does not look right.',
  'auth/missing-email': 'Enter your email address.',
  'auth/network-request-failed': 'No connection. Check your internet and try again.',
  'auth/too-many-requests': 'Too many attempts. Wait a few minutes and try again.',
  'auth/invalid-action-code': 'This sign-in link was already used or is no longer valid. Ask for a new one.',
  'auth/expired-action-code': 'This sign-in link has expired. Ask for a new one.',
  'auth/operation-not-allowed': 'This way of signing in is not available.',
  'auth/admin-restricted-operation': 'This way of signing in is not available.',
}

/** A readable message for a sign-in failure. */
function describe(e: unknown): string {
  const code = errorCode(e)
  return MESSAGES[code] ?? 'Sign-in did not go through. Try again.'
}

/** Sign in as a guest or with an emailed link, and return to the requested path and query, or /new. */
export default function SignIn() {
  const { user, loading } = useAuthState()
  const navigate = useNavigate()
  const from = (useLocation().state as { from?: Location } | null)?.from
  const target = from ? from.pathname + from.search : '/new'
  const [busy, setBusy] = useState<Action | ''>('')
  const [error, setError] = useState('')
  const [email, setEmail] = useState(savedEmail() ?? '')
  const [sentTo, setSentTo] = useState('')
  const [existing, setExisting] = useState(false)
  const [mismatch, setMismatch] = useState(false)
  const finishing = useRef(false)
  const [opened] = useState(isEmailLink)

  /** Run one sign-in action; show failures in the form and allow another attempt. */
  async function run<T>(action: Action, fn: () => Promise<T>, done?: (result: T) => void, failed?: (e: unknown) => void) {
    setBusy(action)
    setError('')
    try {
      done?.(await fn())
    } catch (e) {
      setError(describe(e))
      failed?.(e)
    } finally {
      setBusy('')
    }
  }

  // A sign-in link opened on this page: finish once the saved session is restored, so an anonymous user's
  // jobs are kept. Without the saved address (another browser), the form asks for it.
  useEffect(() => {
    const saved = savedEmail()
    if (loading || !opened || finishing.current || !saved) return
    finishing.current = true
    void run('link', () => completeEmailLink(saved), (result) => {
      setExisting(result === 'existing')
      if (result === 'existing') setSentTo(saved)
    }, (e) => errorCode(e) === 'auth/invalid-email' && setMismatch(true))
  }, [loading, opened])

  if (user && !user.isAnonymous) return <Navigate to={target} replace />

  const saving = !!user?.isAnonymous
  const confirmingLink = opened && (!savedEmail() || mismatch) && !sentTo
  const waiting = loading || busy !== ''

  /** A link that hit an existing account sends a new one: say so, and where to look. */
  function onLink(result: Awaited<ReturnType<typeof completeEmailLink>>) {
    setExisting(result === 'existing')
    if (result === 'existing') setSentTo(savedEmail() ?? email)
  }

  function onEmail(e: FormEvent) {
    e.preventDefault()
    const address = email.trim()
    if (confirmingLink) return void run('link', () => completeEmailLink(address), onLink, (e) => setMismatch(errorCode(e) === 'auth/invalid-email'))
    setExisting(false)
    void run('email', () => sendEmailLink(address), () => setSentTo(address))
  }

  return (
    <div className="mx-auto max-w-md pt-4 sm:pt-12">
      <span className="sticker bg-volt text-ink">{saving ? 'Keep your jobs' : 'Takes one tap'}</span>
      <h1 className="mt-5 text-6xl">{saving ? 'Save your work.' : 'Sign in. Skip the busywork.'}</h1>
      <p className="mt-4 text-lg text-muted-foreground">
        {saving
          ? 'Add your email and the jobs you already sent stay with you. You can pick them up on any device.'
          : 'Your tasks and answers are saved to your account, so you can pick up on any device.'}
      </p>

      {busy === 'link' && !error ? (
        <p role="status" className="mt-8 flex items-center gap-2 text-lg font-bold">
          <LoaderCircle className="animate-spin" aria-hidden="true" /> Signing you in…
        </p>
      ) : (
        <>
          {sentTo ? (
            <div role="status" className="mt-6 rounded-2xl border-2 border-input bg-card p-4">
              <p className="font-bold">Check your inbox.</p>
              {existing && <p className="mt-1 text-muted-foreground">That email already has an account, so this link signs you in to it. Your guest jobs stay with the guest session and will not move to that account.</p>}
              <p className="mt-1 text-muted-foreground">
                We sent a sign-in link to {sentTo}. Open it in this browser to finish.
              </p>
              <Button variant="ghost" className="mt-2" onClick={() => setSentTo('')}>
                Use a different email
              </Button>
            </div>
          ) : (
            <form onSubmit={onEmail} noValidate className="mt-6">
              <label htmlFor="email" className="block font-bold">
                {confirmingLink ? 'Confirm your email to finish signing in' : 'Your email'}
              </label>
              <input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={waiting}
                placeholder="you@school.example"
                className="mt-2 block h-12 w-full rounded-2xl border-2 border-input bg-card px-4 text-base placeholder:text-muted-foreground"
              />
              <Button type="submit" size="lg" className="mt-3 h-14 w-full text-lg" disabled={waiting} aria-busy={busy === 'email'}>
                {busy === 'email' && <LoaderCircle className="animate-spin" aria-hidden="true" />}
                {confirmingLink ? 'Finish signing in' : busy === 'email' ? 'Sending…' : 'Email me a sign-in link'}
              </Button>
            </form>
          )}

          {!saving && (
            <Button variant="ghost" size="lg" className="mt-3 h-12 w-full" onClick={() => void run('guest', signInAsGuest, () => navigate(target, { replace: true }))} disabled={waiting} aria-busy={busy === 'guest'}>
              {busy === 'guest' && <LoaderCircle className="animate-spin" aria-hidden="true" />}
              Continue without an account
            </Button>
          )}
          {saving && (
            <Button asChild variant="ghost" size="lg" className="mt-3 h-12 w-full">
              <Link to={target}>Not now</Link>
            </Button>
          )}
        </>
      )}
      {error && (
        <p role="alert" className="mt-4 font-bold text-coral">
          {error}
        </p>
      )}
    </div>
  )
}
