import { useQuery } from '@tanstack/react-query'
import { getJob, listJobs, type Job } from '@/api/jobs'
import { useAuth } from '@/auth/auth'

export const isPending = (j: Job) => j.status === 'queued' || j.status === 'running'
const POLL_MS = 1000

/** All of the user's jobs; polls while any job is still queued or running. */
export function useJobs() {
  const user = useAuth()
  return useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    enabled: !!user,
    refetchInterval: (q) => (q.state.data?.some(isPending) ? POLL_MS : false),
  })
}

export function useJob(id: string) {
  return useQuery({
    queryKey: ['jobs', id],
    queryFn: () => getJob(id),
    refetchInterval: (q) => (q.state.data && isPending(q.state.data) ? POLL_MS : false),
    retry: (n, e) => !isNotFound(e) && n < 2,
  })
}

export const isNotFound = (e: unknown) => e instanceof Error && /not found/i.test(e.message)

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
export function ago(iso: string) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return rtf.format(-min, 'minute')
  if (min < 1440) return rtf.format(-Math.round(min / 60), 'hour')
  return rtf.format(-Math.round(min / 1440), 'day')
}
