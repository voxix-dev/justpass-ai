import { Component, lazy, Suspense, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronLeft, FlaskConical, LoaderCircle } from 'lucide-react'
import { acceptJob, sendFeedback, type Job } from '@/api/jobs'
import { ErrorState, Skeleton, StatusBadge } from '@/components/bits'
import { Button } from '@/components/ui/button'
import { ago, isConflict, isNotFound, isPending, useJob } from '@/lib/jobs'

const Answer = lazy(() => import('./Answer'))

/** Accept stays off this long after it appears: a double tap on "Do it for me" must not land on it. */
const ACCEPT_DELAY_MS = 600

/** Show the job from the route, resetting the detail view's local state when its ID changes. */
export default function Result() {
  const { id = '' } = useParams()
  return <ResultView key={id} id={id} />
}

/** Load a job and show its detail, a missing-job message, or a retryable fetch error. */
function ResultView({ id }: { id: string }) {
  const { data: job, isPending: loading, error, isFetching, refetch } = useJob(id)

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
      ) : job ? (
        <>
          {error && <p role="status" className="mt-2 font-bold text-coral">Could not refresh. This page may be out of date.</p>}
          <JobDetail job={job} />
        </>
      ) : isNotFound(error) ? (
        <div className="mt-4">
          <h1 className="text-5xl">This job does not exist</h1>
          <p className="mt-2 text-muted-foreground">It may have been removed, or the link is wrong.</p>
          <Button asChild className="mt-6">
            <Link to="/jobs">See my jobs</Link>
          </Button>
        </div>
      ) : (
        <div className="mt-4">
          <ErrorState title="This job did not load." onRetry={() => void refetch()} busy={isFetching} />
        </div>
      )}
    </article>
  )
}

/** What a screen reader hears when the job's state changes. */
function announcement(job: Job) {
  if (job.accepted) return 'Answer accepted.'
  if (job.status === 'queued') return 'Waiting for a solver.'
  if (job.status === 'running') return 'Working through the steps.'
  if (job.status === 'failed') return ''
  return job.round > 1 ? `Revised answer ready, round ${job.round}.` : 'The answer is ready.'
}

/** If an answer cannot be rendered, show its raw text so the student can still read and review it. */
class AnswerBoundary extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? <p className="answer whitespace-pre-wrap">{this.props.text}</p> : this.props.children
  }
}

/** Show the question and current outcome, offering review actions once the answer is on screen. */
function JobDetail({ job }: { job: Job }) {
  return (
    <>
      <header className="mt-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <StatusBadge job={job} />
          <span>{ago(job.createdAt)}</span>
        </div>
        <h1 dir="auto" className="mt-4 text-4xl break-words sm:text-5xl">{job.title}</h1>
        {job.round > 1 && <p className="mt-3 text-muted-foreground">Revised answer, round {job.round}.</p>}
      </header>

      {job.text !== job.title && (
        <section aria-labelledby="q" className="mt-6 rounded-2xl border-2 border-border bg-card p-4">
          <h2 id="q" className="text-xl">The full task</h2>
          <p dir="auto" className="mt-2 whitespace-pre-wrap break-words">{job.text}</p>
        </section>
      )}

      <p aria-live="polite" className="sr-only">
        {announcement(job)}
      </p>
      {/* Accept lives inside Suspense too: it must not appear before the answer does. */}
      <Suspense fallback={<Skeleton className="mt-8 h-64" />}>
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
              <p className="mt-1 text-muted-foreground">
                <Link to="/new" state={{ text: job.text }} className="font-bold text-volt underline underline-offset-4">
                  Send it again
                </Link>
                , maybe worded a different way.
              </p>
            </div>
          ) : (
            job.result && (
              <div id="answer" tabIndex={-1} className="scroll-mt-24 outline-none">
                {job.feedback && (
                  <p dir="auto" className="mb-8 break-words text-muted-foreground italic">
                    Revised after your feedback: “{job.feedback}”
                  </p>
                )}
                <div className="relative">
                  <AnswerBoundary key={job.round} text={job.result}>
                    <Answer markdown={job.result} />
                  </AnswerBoundary>
                  {job.accepted && (
                    <span aria-hidden="true" className="stamp pointer-events-none absolute -top-6 right-3 bg-paper text-4xl text-[#1f8a3b]">
                      PASS
                    </span>
                  )}
                </div>
              </div>
            )
          )}
        </div>

        {job.status === 'done' && <Actions job={job} />}
      </Suspense>
    </>
  )
}

/** Offer acceptance and feedback for a finished answer; feedback revises the same job in a new round. */
function Actions({ job }: { job: Job }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const [error, setError] = useState('')
  const [armed, setArmed] = useState(false)
  const acceptedLine = useRef<HTMLParagraphElement>(null)

  useEffect(() => {
    const timer = setTimeout(() => setArmed(true), ACCEPT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [])

  /** A 409 means the job changed elsewhere (in another tab, say): reload it to show what happened. */
  const reloadIfStale = (e: Error) => {
    if (isConflict(e)) void qc.invalidateQueries({ queryKey: ['jobs', job.id] })
  }

  const accept = useMutation({
    mutationFn: () => acceptJob(job.id, job.round),
    onSuccess: (j) => {
      qc.setQueryData(['jobs', j.id], j)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
    },
    onError: reloadIfStale,
  })
  const feedback = useMutation({
    mutationFn: (text: string) => sendFeedback(job.id, job.round, text),
    onSuccess: (j) => {
      qc.setQueryData(['jobs', j.id], j)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
      setNote('')
      // Take the student to what changed: the revised answer is above this form. focus() alone
      // does not scroll when the bottom of the answer is already on screen.
      const answer = document.getElementById('answer')
      answer?.focus({ preventScroll: true })
      answer?.scrollIntoView()
    },
    onError: (e) => {
      setError(e.message)
      reloadIfStale(e)
    },
  })

  // The Accept button is gone once it worked: keep keyboard and screen reader users in place.
  useEffect(() => {
    if (accept.isSuccess && job.accepted) acceptedLine.current?.focus()
  }, [accept.isSuccess, job.accepted])

  /** Prevent native submission, display an error for blank feedback, and submit a revision otherwise. */
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (feedback.isPending) return
    if (!note.trim()) {
      // A double click lands here too, once the first one has sent the note and cleared the box.
      if (!feedback.isSuccess) setError('Write what should change first.')
      return
    }
    setError('')
    feedback.mutate(note)
  }

  // Accepted is final: the API refuses more feedback (409).
  if (job.accepted)
    return (
      <div className="mt-8 space-y-4">
        <p ref={acceptedLine} tabIndex={-1} className="flex items-center gap-2 font-display text-2xl font-extrabold text-volt outline-none">
          <Check className="size-6" aria-hidden="true" /> Accepted. Done and dusted.
        </p>
        {feedback.isError && <p role="alert" className="font-bold text-coral">This job was accepted somewhere else, so your feedback was not sent.</p>}
        <Link to="/prep" className="flex items-center gap-3 rounded-2xl border-2 border-dashed border-pink/70 p-4 hover:bg-pink/10">
          <FlaskConical className="size-6 shrink-0 text-pink" aria-hidden="true" />
          <span>
            <span className="font-bold text-pink">Soon:</span> accepted answers help JustPass predict your next test.
          </span>
        </Link>
      </div>
    )

  return (
    <div className="mt-8 space-y-8">
      <div>
        <Button size="lg" className="w-full sm:w-auto" onClick={() => !accept.isPending && accept.mutate()} disabled={!armed} aria-disabled={accept.isPending}>
          <Check aria-hidden="true" /> {accept.isPending ? 'Accepting…' : 'Accept answer'}
        </Button>
        {accept.isError && <p role="alert" className="mt-2 font-bold text-coral">{accept.error.message}</p>}
      </div>

      <form onSubmit={onSubmit} noValidate>
        <label htmlFor="feedback" className="block font-bold">
          Something off? Push back
        </label>
        <textarea
          id="feedback"
          value={note}
          onChange={(e) => {
            setNote(e.target.value)
            setError('')
          }}
          rows={3}
          placeholder="Step 2 skips how you got −2 and −3"
          aria-describedby="feedback-error"
          aria-invalid={(!!error && !note.trim()) || undefined}
          className="mt-2 block w-full resize-y rounded-2xl border-2 border-input bg-card p-4 text-base placeholder:text-muted-foreground aria-invalid:border-coral"
        />
        <p id="feedback-error" role="alert" className="mt-2 min-h-6 font-bold text-coral">
          {error}
        </p>
        <Button type="submit" variant="outline" size="lg" className="mt-1 w-full sm:w-auto" aria-disabled={feedback.isPending}>
          {feedback.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
          {feedback.isPending ? 'Sending…' : 'Get a revised answer'}
        </Button>
      </form>
    </div>
  )
}
