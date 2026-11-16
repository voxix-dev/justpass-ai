import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useAuthState } from '@/auth/auth'

/** Drop cached jobs when the signed-in user changes (not on the first load), so no one sees another's data. */
export function ClearOnUserChange() {
  const queryClient = useQueryClient()
  const { user, loading } = useAuthState()
  const uid = user?.uid ?? null
  const seen = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (loading) return
    if (seen.current !== undefined && seen.current !== uid) queryClient.clear()
    seen.current = uid
  }, [loading, uid, queryClient])
  return null
}
