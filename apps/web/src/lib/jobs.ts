import { useQuery } from '@tanstack/react-query'
import { getJob, listJobs, type Job } from '@/api/jobs'
import { useAuth } from '@/auth/auth'

/** Whether a job is still waiting for a terminal result. */
export const isPending = (j: Job) => j.status === 'queued' || j.status === 'running'
const POLL_MS = 1000

/**
 * Query all jobs in the in-memory stub when signed in; they are not filtered by user.
 * Poll every second while any returned job is queued or running.
 */
export function useJobs() {
  const user = useAuth()
  return useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    enabled: !!user,
    refetchInterval: (q) => (q.state.data?.some(isPending) ? POLL_MS : false),
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
    refetchInterval: (q) => (q.state.data && isPending(q.state.data) ? POLL_MS : false),
    retry: (n, e) => !isNotFound(e) && n < 2,
  })
}

/** Recognize Error messages containing "not found", ignoring case. */
export const isNotFound = (e: unknown) => e instanceof Error && /not found/i.test(e.message)

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
/**
 * Format an ISO timestamp relative to now in the browser's locale, rounded to minutes,
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
