// Jobs API used by the screens, called through the generated client. Photos are not supported yet:
// a job is text only. Every function throws an ApiError with a message fit to show the student.
import { api } from './client'
import type { components } from './schema.gen'

export type Job = components['schemas']['JobOut']
export type JobStatus = Job['status']
export type NewJob = components['schemas']['JobCreate']

/** A failed request. status is 0 when no usable response arrived at all. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Turn an error response into a sentence: the API's own, or a readable one for a 422 or the rest. */
function message(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (status !== 422) return `Something went wrong (${status}). Try again.`
  const tooLong = (detail as { type: string; ctx?: { max_length?: number } }[] | undefined)?.find((e) => e.type === 'string_too_long')
  if (tooLong?.ctx?.max_length) return `That is too long. Keep it under ${tooLong.ctx.max_length.toLocaleString('en')} characters.`
  return 'That does not look right. Check it and try again.'
}

/**
 * Return the response body, or throw an ApiError. A request that fails outright (offline, timed
 * out, or a body that is not JSON) gets one plain message instead of the browser's.
 */
async function unwrap<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let result
  try {
    result = await request
  } catch {
    throw new ApiError('Could not reach JustPass. Check your connection and try again.', 0)
  }
  const { data, error, response } = result
  if (error === undefined && data != null) return data
  throw new ApiError(message(error, response.status), response.status)
}

/** Return the user's newest jobs first. */
export async function listJobs(): Promise<Job[]> {
  return unwrap(api.GET('/jobs'))
}

/**
 * Return one job. A malformed ID counts as unknown.
 * @throws {ApiError} If the ID is unknown ("Job not found").
 */
export async function getJob(id: string): Promise<Job> {
  if (!UUID.test(id)) throw new ApiError('Job not found', 404)
  return unwrap(api.GET('/jobs/{job_id}', { params: { path: { job_id: id } } }))
}

/** Send a new task. The API trims the text; the first line becomes the job's title. */
export async function createJob(input: NewJob): Promise<Job> {
  return unwrap(api.POST('/jobs', { body: input }))
}

/**
 * Accept the answer of the round the student saw.
 * @throws {ApiError} If the ID is unknown, or the job is not in review on that round (409).
 */
export async function acceptJob(id: string, round: number): Promise<Job> {
  return unwrap(api.POST('/jobs/{job_id}/accept', { params: { path: { job_id: id } }, body: { round } }))
}

/**
 * Start the next round of the same job with the student's feedback on the round they saw.
 * @throws {ApiError} If the ID is unknown, or the job is accepted, failed or on a newer round (409).
 */
export async function sendFeedback(id: string, round: number, feedback: string): Promise<Job> {
  return unwrap(api.POST('/jobs/{job_id}/feedback', { params: { path: { job_id: id } }, body: { text: feedback, round } }))
}
