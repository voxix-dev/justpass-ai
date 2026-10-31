import { Link } from 'react-router'
import { FlaskConical, PenLine } from 'lucide-react'
import type { Job } from '@/api/jobs'
import { ErrorState, Skeleton, StatusBadge } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { ago, isPending as pending, useJobs } from '@/lib/jobs'

/** Rough time a student would spend on one task by hand. An estimate, and labelled as one. */
const MIN_PER_TASK = 20
/** The API returns this many jobs at most. */
const LIST_LIMIT = 100

/** Show job history and summary stats, with loading, retry, and empty states. */
export default function Jobs() {
  const { data, isPending, isError, isFetching, refetch } = useJobs()

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-end justify-between gap-4">
        <h1 className="text-6xl">My jobs</h1>
        {!!data?.length && (
          <Button asChild variant="outline" className="hidden sm:inline-flex">
            <Link to="/new">
              <PenLine aria-hidden="true" /> Send a task
            </Link>
          </Button>
        )}
      </div>

      <div className="mt-8">
        {isPending ? (
          <div role="status" className="space-y-px overflow-hidden rounded-2xl border-2 border-border bg-card">
            <span className="sr-only">Loading your jobs…</span>
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-3 p-4">
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/4" />
                </div>
                <Skeleton className="h-6 w-16" />
              </div>
            ))}
          </div>
        ) : !data ? (
          <ErrorState title="Your jobs did not load." onRetry={() => void refetch()} busy={isFetching} />
        ) : data.length === 0 ? (
          <div className="rounded-2xl border-2 border-dashed border-input p-8 text-center">
            <h2 className="text-3xl">Nothing here yet</h2>
            <p className="mx-auto mt-2 max-w-[34ch] text-muted-foreground">Send your first task. The worked answer lands here, and the clock on time saved starts.</p>
            <Button asChild size="lg" className="mt-6">
              <Link to="/new">Send a task</Link>
            </Button>
          </div>
        ) : (
          <>
            {isError && <p role="status" className="mb-4 font-bold text-coral">Could not refresh. This list may be out of date.</p>}
            {data.length >= LIST_LIMIT && <p className="mb-4 text-muted-foreground">Showing your newest {LIST_LIMIT} jobs. The numbers below count only these.</p>}
            <Stats jobs={data} />
            <ul className="mt-6 divide-y-2 divide-border overflow-hidden rounded-2xl border-2 border-border bg-card">
              {data.map((job) => (
                <li key={job.id}>
                  <Link to={`/jobs/${job.id}`} className="flex min-h-18 items-center gap-3 px-4 py-3 hover:bg-raised focus-visible:-outline-offset-4">
                    <div className="min-w-0 flex-1">
                      <p dir="auto" className="truncate font-bold">{job.title}</p>
                      <p className="text-sm text-muted-foreground">
                        Sent {ago(job.createdAt)}
                        {job.round > 1 && `, round ${job.round}`}
                      </p>
                    </div>
                    <StatusBadge job={job} />
                  </Link>
                </li>
              ))}
            </ul>
            <Link to="/prep" className="mt-6 flex items-center gap-3 rounded-2xl border-2 border-dashed border-pink/70 p-4 hover:bg-pink/10">
              <FlaskConical className="size-6 shrink-0 text-pink" aria-hidden="true" />
              <span>
                <span className="font-bold text-pink">Soon:</span> accepted answers help JustPass predict your next test.
                <span className="block text-sm text-muted-foreground">See what's in the lab</span>
              </span>
            </Link>
          </>
        )}
      </div>
    </div>
  )
}

/** Show job counts and estimated time saved at 20 minutes per successfully finished job. */
function Stats({ jobs }: { jobs: Job[] }) {
  const done = jobs.filter((j) => j.status === 'done').length
  const working = jobs.filter(pending).length
  const accepted = jobs.filter((j) => j.accepted).length
  const min = done * MIN_PER_TASK
  const saved = min < 60 ? `${min} min` : `${Math.floor(min / 60)} h${min % 60 ? ` ${min % 60} min` : ''}`
  return (
    <section aria-label="Your stats" className="grid grid-cols-3 gap-3">
      <div className="col-span-3 rounded-2xl bg-volt p-5 text-ink">
        <p className="font-bold">Time saved</p>
        <p className="font-display text-6xl leading-none font-extrabold [font-stretch:80%]">~{saved}</p>
        <p className="mt-2 text-sm font-semibold">Estimate: about {MIN_PER_TASK} minutes for each finished task.</p>
      </div>
      {[
        ['Done', done],
        ['In progress', working],
        ['Accepted', accepted],
      ].map(([label, n]) => (
        <div key={label} className="rounded-2xl border-2 border-border bg-card p-3">
          <p className="font-display text-4xl leading-none font-extrabold">{n}</p>
          <p className="mt-1 text-sm text-muted-foreground">{label}</p>
        </div>
      ))}
    </section>
  )
}
