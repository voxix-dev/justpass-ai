// Jobs API used by the screens. The Jobs API isn't in contracts/openapi.json yet, so this is an
// in-memory fake with the shape we expect. When the endpoints land, regenerate schema.gen.ts and
// reimplement these functions with `api` from ./client; the screens don't change.

export type JobStatus = 'queued' | 'running' | 'done' | 'failed'

export type Job = {
  id: string
  status: JobStatus
  createdAt: string
  /** First line of the question, for lists. */
  title: string
  text: string
  /** Shrunk JPEG as an object URL (fake only; the real API will return a signed URL). */
  imageUrl: string | null
  /** Markdown with $inline$ and $$display$$ math, set when status is 'done'. */
  result: string | null
  error: string | null
  accepted: boolean
  /** The job this one revises, when it was started from feedback. */
  parentId: string | null
  feedback: string | null
}

export type NewJob = { text: string; image: Blob | null }

const jobs = new Map<string, Job & { startedAt: number; fails: boolean }>()

const QUEUED_MS = 1500
const DONE_MS = 6000

const SAMPLE = `## Solution

We need the roots of $x^2 - 5x + 6 = 0$.

**1. Factor.** Look for two numbers that multiply to $6$ and add to $-5$: those are $-2$ and $-3$.

$$
x^2 - 5x + 6 = (x - 2)(x - 3)
$$

**2. Solve.** A product is zero when one factor is zero, so $x = 2$ or $x = 3$.

**3. Check** with the quadratic formula:

$$
x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a} = \\frac{5 \\pm \\sqrt{25 - 24}}{2} = \\frac{5 \\pm 1}{2}
$$

> **Answer:** $x \\in \\{2, 3\\}$

### Why it works
The sum of the roots is $-\\tfrac{b}{a} = 5$ and the product is $\\tfrac{c}{a} = 6$ (Vieta's formulas).
`

/** Simulate network latency; the delay is in milliseconds. */
const delay = (ms = 300) => new Promise((r) => setTimeout(r, ms))

/**
 * Return a copy with simulated progress: queued before 1.5 s, running until 6 s,
 * then failed or done with a sample answer. Does not update the stored job.
 * @throws {Error} If the ID is unknown ("Job not found").
 */
function snapshot(id: string): Job {
  const j = jobs.get(id)
  if (!j) throw new Error('Job not found')
  const age = Date.now() - j.startedAt
  const { startedAt: _, fails, ...job } = j
  if (age < QUEUED_MS) return { ...job, status: 'queued' }
  if (age < DONE_MS) return { ...job, status: 'running' }
  if (fails) return { ...job, status: 'failed', error: 'The solver could not read this question.' }
  const revised = job.feedback ? `_Revised after your feedback: “${job.feedback}”_\n\n${SAMPLE}` : SAMPLE
  return { ...job, status: 'done', result: revised }
}

/**
 * Store a new in-memory job and return its initial snapshot. The first line becomes
 * its title, truncated to 80 characters; an empty first line uses "Photo question".
 * The whole word "fail" in the question, ignoring case, simulates solver failure.
 * parentId links a revision to its source; nonempty feedback appears in a successful answer.
 */
function add(text: string, imageUrl: string | null, parentId: string | null, feedback: string | null): Job {
  const id = crypto.randomUUID()
  jobs.set(id, {
    id,
    status: 'queued',
    createdAt: new Date().toISOString(),
    title: text.split('\n')[0]?.slice(0, 80) || 'Photo question',
    text,
    imageUrl,
    result: null,
    error: null,
    accepted: false,
    parentId,
    feedback,
    startedAt: Date.now(),
    // Type "fail" in a question to see the error state.
    fails: /\bfail\b/i.test(text),
  })
  return snapshot(id)
}

/** Return current snapshots of all jobs in this module's memory, newest first. */
export async function listJobs(): Promise<Job[]> {
  await delay()
  return [...jobs.keys()].map(snapshot).sort((a, b) => b.createdAt.localeCompare(a.createdAt))
}

/**
 * Return a job's simulated progress at the time it is read.
 * @throws {Error} If the ID is unknown ("Job not found").
 */
export async function getJob(id: string): Promise<Job> {
  await delay()
  return snapshot(id)
}

/**
 * Trim the question and store a new in-memory job, optionally with a photo.
 * Creates an object URL for the supplied image without resizing it or revoking the URL.
 * @throws {Error} If both trimmed text and image are absent ("Add a question or a photo.").
 */
export async function createJob(input: NewJob): Promise<Job> {
  await delay(600)
  if (!input.text.trim() && !input.image) throw new Error('Add a question or a photo.')
  const text = input.text.trim()
  return add(text, input.image ? URL.createObjectURL(input.image) : null, null, null)
}

/**
 * Mark a stored job accepted, regardless of its status, and return its current snapshot.
 * @throws {Error} If the ID is unknown ("Job not found").
 */
export async function acceptJob(id: string): Promise<Job> {
  await delay()
  const j = jobs.get(id)
  if (!j) throw new Error('Job not found')
  j.accepted = true
  return snapshot(id)
}

/**
 * Starts a new run of the same question with the student's feedback.
 * Reuses the parent's photo URL, trims feedback (which may be empty), and returns
 * the new job linked to the parent. The parent is unchanged.
 * @throws {Error} If the parent ID is unknown ("Job not found").
 */
export async function sendFeedback(id: string, feedback: string): Promise<Job> {
  await delay(600)
  const parent = jobs.get(id)
  if (!parent) throw new Error('Job not found')
  return add(parent.text, parent.imageUrl, id, feedback.trim())
}
