import { Link } from 'react-router'
import { PenLine } from 'lucide-react'
import { ErrorState, Skeleton, StatusBadge } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { ago, useJobs } from '@/lib/jobs'

export default function Jobs() {
  const { data, isPending, isError, refetch } = useJobs()

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-3xl sm:text-4xl">My jobs</h1>
        {!!data?.length && (
          <Button asChild variant="outline" className="hidden sm:inline-flex">
            <Link to="/new">
              <PenLine aria-hidden="true" /> Ask a question
            </Link>
          </Button>
        )}
      </div>

      <div className="mt-6">
        {isPending ? (
          <div role="status" className="space-y-px overflow-hidden rounded-xl border bg-card">
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
        ) : isError ? (
          <ErrorState title="Your jobs did not load." onRetry={() => void refetch()} />
        ) : data.length === 0 ? (
          <div className="rounded-xl border-2 border-dashed border-input p-8 text-center">
            <h2 className="text-xl">No questions yet</h2>
            <p className="mx-auto mt-2 max-w-[34ch] text-muted-foreground">
              Ask your first one and the worked solution will show up here.
            </p>
            <Button asChild size="lg" className="mt-6">
              <Link to="/new">Ask a question</Link>
            </Button>
          </div>
        ) : (
          <ul className="divide-y overflow-hidden rounded-xl border bg-card">
            {data.map((job) => (
              <li key={job.id}>
                <Link to={`/jobs/${job.id}`} className="flex min-h-18 items-center gap-3 px-4 py-3 hover:bg-muted focus-visible:-outline-offset-3">
                  {job.imageUrl && <img src={job.imageUrl} alt="" className="size-11 shrink-0 rounded-md border object-cover" />}
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-bold">{job.title}</p>
                    <p className="text-sm text-muted-foreground">
                      {job.parentId ? 'Revised' : 'Asked'} {ago(job.createdAt)}
                    </p>
                  </div>
                  <StatusBadge job={job} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
