import { useState } from 'react'
import { Navigate, useLocation, useNavigate, type Location } from 'react-router'
import { LoaderCircle } from 'lucide-react'
import { signIn, useAuth } from '@/auth/auth'
import { Button } from '@/components/ui/button'

export default function SignIn() {
  const user = useAuth()
  const navigate = useNavigate()
  const from = (useLocation().state as { from?: Location } | null)?.from
  const target = from ? from.pathname + from.search : '/new'
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  if (user && !busy) return <Navigate to={target} replace />

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
    <div className="mx-auto max-w-sm pt-6 sm:pt-12">
      <h1 className="text-3xl">Sign in to JustPass</h1>
      <p className="mt-3 text-muted-foreground">Your questions and answers are saved to your account, so you can pick up on any device.</p>
      <Button size="lg" className="mt-8 w-full" onClick={go} disabled={busy} aria-busy={busy}>
        {busy && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {busy ? 'Signing in…' : 'Continue'}
      </Button>
      {error && (
        <p role="alert" className="mt-4 font-semibold text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
