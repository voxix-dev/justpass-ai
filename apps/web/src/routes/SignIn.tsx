import { useState } from 'react'
import { Navigate, useLocation, useNavigate, type Location } from 'react-router'
import { LoaderCircle } from 'lucide-react'
import { signIn, useAuth } from '@/auth/auth'
import { Button } from '@/components/ui/button'

/** Sign in to the development stub and return to the requested path and query, or /new. */
export default function SignIn() {
  const user = useAuth()
  const navigate = useNavigate()
  const from = (useLocation().state as { from?: Location } | null)?.from
  const target = from ? from.pathname + from.search : '/new'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (user && !busy) return <Navigate to={target} replace />

  /** Sign in and replace the current route; show failures in the form and allow another attempt. */
  async function go() {
    setBusy(true)
    setError('')
    try {
      await signIn()
      navigate(target, { replace: true })
    } catch {
      setError('Sign-in did not go through. Try again.')
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-md pt-4 sm:pt-12">
      <span className="sticker bg-volt text-ink">Takes one tap</span>
      <h1 className="mt-5 text-6xl">Sign in. Skip the busywork.</h1>
      <p className="mt-4 text-lg text-muted-foreground">Your tasks and answers are saved to your account, so you can pick up on any device.</p>
      <Button size="lg" className="mt-8 h-14 w-full text-lg" onClick={go} disabled={busy} aria-busy={busy}>
        {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {busy ? 'Signing in…' : 'Continue'}
      </Button>
      {error && (
        <p role="alert" className="mt-4 font-bold text-coral">
          {error}
        </p>
      )}
    </div>
  )
}
