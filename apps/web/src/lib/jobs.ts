import { useQuery } from '@tanstack/react-query'
import { ApiError, getJob, listJobs, type Job } from '@/api/jobs'
import { useAuth } from '@/auth/auth'

/** Whether a job is still waiting for a terminal result. */
export const isPending = (j: Job) => j.status === 'queued' || j.status === 'running'
const POLL_MS = 1000
/** Poll more slowly while requests are failing, so a struggling API is not hammered. */
const pollMs = (failing: boolean) => (failing ? 10_000 : POLL_MS)

/**
 * Query the user's jobs, newest first, when signed in.
 * Poll every second while any returned job is queued or running.
 */
export function useJobs() {
  const user = useAuth()
  return useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    enabled: !!user,
    refetchInterval: (q) => (q.state.data?.some(isPending) ? pollMs(!!q.state.error) : false),
  })
}

/**
 * Query one job, polling every second while it is queued or running.
 * Expose fetch failures in the query result; skip retries for "not found" errors
 * and retry other failures at most twice.
 */
export function useJob(id: string) {
  return useQuery({
    queryKey: ['jobs', id],
    queryFn: () => getJob(id),
    refetchInterval: (q) => (q.state.data && isPending(q.state.data) ? pollMs(!!q.state.error) : false),
    retry: (n, e) => !isNotFound(e) && n < 2,
  })
}

/** Recognize Error messages containing "not found", ignoring case. */
export const isNotFound = (e: unknown) => e instanceof Error && /not found/i.test(e.message)
/** The job changed elsewhere, so what's on screen is out of date. */
export const isConflict = (e: unknown) => e instanceof ApiError && e.status === 409
/** The API refused the text itself. */
export const isBadInput = (e: unknown) => e instanceof ApiError && e.status === 422

// English like the rest of the app, not the browser's language.
const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
/**
 * Format an ISO timestamp relative to now, rounded to minutes,
 * hours, or days. Future times and ages rounding below one minute return "just now".
 * @throws {RangeError} If the timestamp cannot be parsed into a valid date.
 */
export function ago(iso: string) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return rtf.format(-min, 'minute')
  if (min < 1440) return rtf.format(-Math.round(min / 60), 'hour')
  return rtf.format(-Math.round(min / 1440), 'day')
}
