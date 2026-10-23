import { lazy, Suspense, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, LoaderCircle } from 'lucide-react'
import { acceptJob, sendFeedback, type Job } from '@/api/jobs'
import { ErrorState, Skeleton, StatusBadge } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { ago, isNotFound, isPending, useJob } from '@/lib/jobs'

const Answer = lazy(() => import('./Answer'))

export default function Result() {
  const { id = '' } = useParams()
  return <ResultView key={id} id={id} />
}

function ResultView({ id }: { id: string }) {
  const { data: job, isPending: loading, error, refetch } = useJob(id)

  return (
    <article className="mx-auto max-w-2xl">
      <Link to="/jobs" className="-ml-2 inline-flex h-11 items-center gap-1 rounded-lg px-2 font-semibold text-muted-foreground hover:text-ink">
        <ChevronLeft className="size-5" aria-hidden="true" /> My jobs
      </Link>
      {loading ? (
        <div role="status" className="mt-4 space-y-4">
          <span className="sr-only">Loading…</span>
          <Skeleton className="h-9 w-4/5" />
          <Skeleton className="h-24" />
        </div>
      ) : error ? (
        isNotFound(error) ? (
          <div className="mt-4">
            <h1 className="text-3xl">This job does not exist</h1>
            <p className="mt-2 text-muted-foreground">It may have been removed, or the link is wrong.</p>
            <Button asChild className="mt-6">
              <Link to="/jobs">See my jobs</Link>
            </Button>
          </div>
        ) : (
          <div className="mt-4">
            <ErrorState title="This job did not load." onRetry={() => void refetch()} />
          </div>
        )
      ) : (
        <JobDetail job={job} />
      )}
    </article>
  )
}

function JobDetail({ job }: { job: Job }) {
  return (
    <>
      <header className="mt-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <StatusBadge job={job} />
          <span>{ago(job.createdAt)}</span>
        </div>
        <h1 className="mt-3 text-2xl break-words sm:text-3xl">{job.title}</h1>
        {job.parentId && (
          <p className="mt-3 text-muted-foreground">
            Revised from{' '}
            <Link to={`/jobs/${job.parentId}`} className="font-semibold text-ink underline underline-offset-4">
              the earlier answer
            </Link>
            .
          </p>
        )}
      </header>

      {(job.text !== job.title || job.imageUrl) && (
        <section aria-labelledby="q" className="mt-6 rounded-xl border bg-card p-4">
          <h2 id="q" className="text-base">Your question</h2>
          {job.text && <p className="mt-2 whitespace-pre-wrap break-words">{job.text}</p>}
          {job.imageUrl && (
            <a href={job.imageUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block rounded-lg">
              <img src={job.imageUrl} alt="Photo of the question (opens full size)" className="max-h-56 rounded-lg border" />
            </a>
          )}
        </section>
      )}

      <p aria-live="polite" className="sr-only">
        {job.status === 'done' ? 'The answer is ready.' : ''}
      </p>
      <div className="mt-8">
        {isPending(job) ? (
          <div className="rounded-xl border-2 border-dashed border-input p-6">
            <p className="flex items-center gap-2 text-lg font-bold text-ink">
              <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
              {job.status === 'queued' ? 'Waiting for a solver…' : 'Working through the steps…'}
            </p>
            <p className="mt-2 text-muted-foreground">You can leave this page. We will let you know when it is ready.</p>
          </div>
        ) : job.status === 'failed' ? (
          <div role="alert" className="rounded-xl border-2 border-destructive/50 bg-card p-5">
            <h2 className="text-xl text-destructive">Could not solve this one</h2>
            <p className="mt-2">{job.error ?? 'Something went wrong while solving.'}</p>
            <p className="mt-1 text-muted-foreground">Add details below and we will try again, or ask it in a different way.</p>
          </div>
        ) : (
          job.result && (
            <Suspense fallback={<Skeleton className="h-64" />}>
              <Answer markdown={job.result} />
            </Suspense>
          )
        )}
      </div>

      {!isPending(job) && <Actions job={job} />}
    </>
  )
}

function Actions({ job }: { job: Job }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const done = job.status === 'done'

  const accept = useMutation({
    mutationFn: () => acceptJob(job.id),
    onSuccess: (j) => {
      qc.setQueryData(['jobs', j.id], j)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
    },
  })
  const feedback = useMutation({
    mutationFn: (text: string) => sendFeedback(job.id, text),
    onSuccess: (j) => {
      qc.setQueryData(['jobs', j.id], j)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
      navigate(`/jobs/${j.id}`)
    },
    onError: (e) => setError(e.message),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!note.trim()) return setError('Write what should change first.')
    setError('')
    feedback.mutate(note)
  }

  return (
    <div className="mt-10 space-y-8 border-t pt-8">
      {done &&
        (job.accepted ? (
          <p className="flex items-center gap-2 font-bold text-ink">
            <Check className="size-5" aria-hidden="true" /> You accepted this answer.
          </p>
        ) : (
          <div>
            <Button size="lg" className="w-full sm:w-auto" onClick={() => accept.mutate()} disabled={accept.isPending}>
              <Check aria-hidden="true" /> {accept.isPending ? 'Accepting…' : 'Accept answer'}
            </Button>
            {accept.isError && <p role="alert" className="mt-2 font-semibold text-destructive">Could not accept. Try again.</p>}
          </div>
        ))}

      <form onSubmit={onSubmit} noValidate>
        <label htmlFor="feedback" className="block font-bold">
          {done ? 'Something off? Say what to fix' : 'Tell us more about the question'}
        </label>
        <textarea
          id="feedback"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder={done ? 'Step 2 skips how you got −2 and −3' : 'It is question 4b on the photo'}
          aria-describedby="feedback-error"
          aria-invalid={!!error || undefined}
          className="mt-2 block w-full resize-y rounded-xl border-2 border-input bg-card p-3 text-base placeholder:text-muted-foreground aria-invalid:border-destructive"
        />
        <p id="feedback-error" role="alert" className="mt-2 min-h-6 font-semibold text-destructive">
          {error}
        </p>
        <Button type="submit" variant={done ? 'outline' : 'default'} size="lg" className="mt-1 w-full sm:w-auto" disabled={feedback.isPending}>
          {feedback.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
          {feedback.isPending ? 'Sending…' : done ? 'Send feedback' : 'Try again'}
        </Button>
      </form>
    </div>
  )
}
