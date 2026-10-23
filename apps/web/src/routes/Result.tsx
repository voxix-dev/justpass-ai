import { lazy, Suspense, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, FlaskConical, LoaderCircle } from 'lucide-react'
import { acceptJob, sendFeedback, type Job } from '@/api/jobs'
import { ErrorState, Skeleton, StatusBadge } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { ago, isNotFound, isPending, useJob } from '@/lib/jobs'

const Answer = lazy(() => import('./Answer'))

/** Show the job from the route, resetting the detail view's local state when its ID changes. */
export default function Result() {
  const { id = '' } = useParams()
  return <ResultView key={id} id={id} />
}

/** Load a job and show its detail, a missing-job message, or a retryable fetch error. */
function ResultView({ id }: { id: string }) {
  const { data: job, isPending: loading, error, refetch } = useJob(id)

  return (
    <article className="mx-auto max-w-2xl">
      <Link to="/jobs" className="-ml-2 inline-flex h-11 items-center gap-1 rounded-lg px-2 font-bold text-muted-foreground hover:text-foreground">
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
            <h1 className="text-5xl">This job does not exist</h1>
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

/** Show the question and current outcome, offering review actions once the job is no longer pending. */
function JobDetail({ job }: { job: Job }) {
  return (
    <>
      <header className="mt-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <StatusBadge job={job} />
          <span>{ago(job.createdAt)}</span>
        </div>
        <h1 className="mt-4 text-4xl break-words sm:text-5xl">{job.title}</h1>
        {job.parentId && (
          <p className="mt-3 text-muted-foreground">
            Revised from{' '}
            <Link to={`/jobs/${job.parentId}`} className="font-bold text-volt underline underline-offset-4">
              the earlier answer
            </Link>
            .
          </p>
        )}
      </header>

      {(job.text !== job.title || job.imageUrl) && (
        <section aria-labelledby="q" className="mt-6 rounded-2xl border-2 border-border bg-card p-4">
          <h2 id="q" className="text-xl">{job.imageUrl ? 'Your photo' : 'The full task'}</h2>
          {job.text !== job.title && <p className="mt-2 whitespace-pre-wrap break-words">{job.text}</p>}
          {job.imageUrl && (
            <a href={job.imageUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block rounded-lg">
              <img src={job.imageUrl} alt="Photo of the question (opens full size)" className="max-h-40 max-w-full rounded-lg" />
            </a>
          )}
        </section>
      )}

      <p aria-live="polite" className="sr-only">
        {job.status === 'done' ? 'The answer is ready.' : ''}
      </p>
      <div className="mt-8">
        {isPending(job) ? (
          <div className="rounded-2xl border-2 border-volt/60 bg-card p-6">
            <p className="flex items-center gap-2 font-display text-2xl font-extrabold text-volt">
              <LoaderCircle className="size-6 animate-spin" aria-hidden="true" />
              {job.status === 'queued' ? 'Waiting for a solver…' : 'Working through the steps…'}
            </p>
            <p className="mt-2 text-muted-foreground">Go do something else. You'll get a ping when it's ready, wherever you are in the app.</p>
          </div>
        ) : job.status === 'failed' ? (
          <div role="alert" className="rounded-2xl border-2 border-coral bg-card p-5">
            <h2 className="text-3xl text-coral">Could not solve this one</h2>
            <p className="mt-2">{job.error ?? 'Something went wrong while solving.'}</p>
            <p className="mt-1 text-muted-foreground">Add details below and we will try again, or ask it in a different way.</p>
          </div>
        ) : (
          job.result && (
            <div className="relative">
              <Suspense fallback={<Skeleton className="h-64" />}>
                <Answer markdown={job.result} />
              </Suspense>
              {job.accepted && (
                <span aria-hidden="true" className="stamp pointer-events-none absolute -top-6 right-3 bg-paper text-4xl text-[#1f8a3b]">
                  PASS
                </span>
              )}
            </div>
          )
        )}
      </div>

      {!isPending(job) && <Actions job={job} />}
    </>
  )
}

/** Offer answer acceptance and feedback; cache mutations and navigate to a new revision on feedback. */
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

  /** Prevent native submission, display an error for blank feedback, and submit a revision otherwise. */
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!note.trim()) return setError('Write what should change first.')
    setError('')
    feedback.mutate(note)
  }

  return (
    <div className="mt-8 space-y-8">
      {done &&
        (job.accepted ? (
          <div className="space-y-4">
            <p className="flex items-center gap-2 font-display text-2xl font-extrabold text-volt">
              <Check className="size-6" aria-hidden="true" /> Accepted. Done and dusted.
            </p>
            <Link to="/prep" className="flex items-center gap-3 rounded-2xl border-2 border-dashed border-pink/70 p-4 hover:bg-pink/10">
              <FlaskConical className="size-6 shrink-0 text-pink" aria-hidden="true" />
              <span>
                <span className="font-bold text-pink">Soon:</span> accepted answers help JustPass predict your next test.
              </span>
            </Link>
          </div>
        ) : (
          <div>
            <Button size="lg" className="w-full sm:w-auto" onClick={() => accept.mutate()} disabled={accept.isPending}>
              <Check aria-hidden="true" /> {accept.isPending ? 'Accepting…' : 'Accept answer'}
            </Button>
            {accept.isError && <p role="alert" className="mt-2 font-bold text-coral">Could not accept. Try again.</p>}
          </div>
        ))}

      <form onSubmit={onSubmit} noValidate>
        <label htmlFor="feedback" className="block font-bold">
          {done ? 'Something off? Push back' : 'Tell us more about the task'}
        </label>
        <textarea
          id="feedback"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder={done ? 'Step 2 skips how you got −2 and −3' : 'It is question 4b on the photo'}
          aria-describedby="feedback-error"
          aria-invalid={!!error || undefined}
          className="mt-2 block w-full resize-y rounded-2xl border-2 border-input bg-card p-4 text-base placeholder:text-muted-foreground aria-invalid:border-coral"
        />
        <p id="feedback-error" role="alert" className="mt-2 min-h-6 font-bold text-coral">
          {error}
        </p>
        <Button type="submit" variant={done ? 'outline' : 'default'} size="lg" className="mt-1 w-full sm:w-auto" disabled={feedback.isPending}>
          {feedback.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
          {feedback.isPending ? 'Sending…' : done ? 'Get a revised answer' : 'Try again'}
        </Button>
      </form>
    </div>
  )
}
