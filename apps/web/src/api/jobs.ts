// Jobs API used by the screens, called through the generated client. Photos are not supported yet:
// a job is text only. Every function throws an Error with the API's message on a failed request.
import { api } from './client'
import type { components } from './schema.gen'

export type Job = components['schemas']['JobOut']
export type JobStatus = Job['status']
export type NewJob = components['schemas']['JobCreate']

/**
 * Return the response body, or throw an Error with the API's `detail` message when it is a string
 * (e.g. "Job not found"). Validation errors (422) and others get a short readable message.
 */
function unwrap<T>({ data, error, response }: { data?: T; error?: unknown; response: Response }): T {
  if (error === undefined && data !== undefined) return data
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') throw new Error(detail)
  if (response.status === 422) throw new Error('That does not look right. Check it and try again.')
  throw new Error(`Something went wrong (${response.status}). Try again.`)
}

/** Return the user's newest jobs first. */
export async function listJobs(): Promise<Job[]> {
  return unwrap(await api.GET('/jobs'))
}

/**
 * Return one job. A malformed ID counts as unknown.
 * @throws {Error} If the ID is unknown ("Job not found").
 */
export async function getJob(id: string): Promise<Job> {
  const r = await api.GET('/jobs/{job_id}', { params: { path: { job_id: id } } })
  if (r.response.status === 422) throw new Error('Job not found')
  return unwrap(r)
}

/** Send a new task. The API trims the text; the first line becomes the job's title. */
export async function createJob(input: NewJob): Promise<Job> {
  return unwrap(await api.POST('/jobs', { body: input }))
}

/**
 * Accept the answer under review.
 * @throws {Error} If the ID is unknown, or the job is not in review (409).
 */
export async function acceptJob(id: string): Promise<Job> {
  return unwrap(await api.POST('/jobs/{job_id}/accept', { params: { path: { job_id: id } } }))
}

/**
 * Start the next round of the same job with the student's feedback, and return the job.
 * @throws {Error} If the ID is unknown, or the job is accepted or failed (409).
 */
export async function sendFeedback(id: string, feedback: string): Promise<Job> {
  return unwrap(await api.POST('/jobs/{job_id}/feedback', { params: { path: { job_id: id } }, body: { text: feedback } }))
}
