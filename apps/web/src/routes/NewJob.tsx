import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Camera, LoaderCircle, X } from 'lucide-react'
import { ApiError, createJob, uploadPhoto } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { isBadInput } from '@/lib/jobs'
import { shrinkImage } from '@/lib/image'

const MAX_PHOTOS = 5

/** A picked photo: its preview, and whether it is on the server (id), on its way, or failed. */
type Photo = { key: number; url: string; id?: string; error?: string }

/** Collect the task as text (prefilled when sent again), cache the job and open its result. */
export default function NewJob() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const again = (useLocation().state as { text?: string } | null)?.text
  const [text, setText] = useState(again ?? '')
  const [error, setError] = useState('')
  const [photos, setPhotos] = useState<Photo[]>([])
  const nextKey = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const live = useRef<Photo[]>([])
  const uploading = photos.some((p) => !p.id && !p.error)

  useEffect(() => {
    live.current = photos
  }, [photos])
  useEffect(() => () => live.current.forEach((p) => URL.revokeObjectURL(p.url)), [])

  const patch = (key: number, change: Partial<Photo>) => setPhotos((all) => all.map((p) => (p.key === key ? { ...p, ...change } : p)))

  /** Shrink and upload each picked photo at once, up to the limit. */
  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []).slice(0, MAX_PHOTOS - photos.length)
    e.target.value = ''
    setError('')
    for (const file of files) {
      const key = nextKey.current++
      setPhotos((all) => [...all, { key, url: URL.createObjectURL(file) }])
      shrinkImage(file)
        .then(uploadPhoto)
        .then((f) => patch(key, { id: f.id }))
        .catch((err: unknown) => patch(key, { error: err instanceof ApiError ? err.message : 'Could not add this photo. Remove it and try another.' }))
    }
  }

  function remove(key: number) {
    const gone = photos.find((p) => p.key === key)
    if (gone) URL.revokeObjectURL(gone.url)
    setPhotos((all) => all.filter((p) => p.key !== key))
    setError('')
  }

  const submit = useMutation({
    mutationFn: createJob,
    onSuccess: (job) => {
      qc.setQueryData(['jobs', job.id], job)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
    },
    onError: (e) => setError(e.message || 'Could not send your task. Try again.'),
  })

  /** Prevent native submission and submit only when the task has text, and only once. */
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (submit.isPending || submit.isSuccess) return
    if (uploading) return setError('Wait for the photos to finish uploading.')
    if (photos.some((p) => p.error)) return setError('Remove the photos that failed, or add them again.')
    if (!text.trim() && !photos.length) return setError('Add the task as text or a photo.')
    setError('')
    // Not in useMutation: this one is skipped when the student has left the page meanwhile.
    submit.mutate({ text: text.trim() ? text : null, photos: photos.flatMap((p) => (p.id ? [p.id] : [])) }, { onSuccess: (job) => navigate(`/jobs/${job.id}`) })
  }

  const invalid = !!error && ((!text.trim() && !photos.length) || isBadInput(submit.error))
  return (
    <form onSubmit={onSubmit} noValidate className="mx-auto max-w-xl">
      <h1 className="text-6xl">What's the task?</h1>
      <p className="mt-3 text-lg text-muted-foreground">Paste it, type it, or snap a photo.</p>
      <label htmlFor="question" className="mt-8 block font-bold">
        The task
      </label>
      <textarea
        id="question"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setError('')
        }}
        rows={6}
        placeholder="Solve 2x² − 8x + 6 = 0 and show each step"
        aria-describedby={error ? 'form-error' : undefined}
        aria-invalid={invalid || undefined}
        className="mt-2 block w-full resize-y rounded-2xl border-2 border-input bg-card p-4 text-lg placeholder:text-muted-foreground aria-invalid:border-coral"
      />
      <div className="mt-4">
        <ul className="flex flex-wrap gap-3">
          {photos.map((p, i) => (
            <li key={p.key} className="relative">
              <img src={p.url} alt="" className={`size-24 rounded-xl border-2 object-cover ${p.error ? 'border-coral' : 'border-input'} ${p.id ? '' : 'opacity-60'}`} />
              {!p.id && !p.error && <LoaderCircle className="absolute inset-0 m-auto size-6 animate-spin" aria-label={`Uploading photo ${i + 1}`} role="img" />}
              {p.error && <span role="img" aria-label={`Photo ${i + 1} failed`} className="absolute inset-x-0 bottom-0 rounded-b-lg bg-coral px-1 text-center text-xs font-bold text-white">Failed</span>}
              <button
                type="button"
                onClick={() => remove(p.key)}
                aria-label={`Remove photo ${i + 1}`}
                className="absolute -top-2 -right-2 flex size-8 items-center justify-center rounded-full border-2 border-input bg-card"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        {photos.length < MAX_PHOTOS && (
          <Button type="button" variant="outline" className="mt-3" onClick={() => fileInput.current?.click()}>
            <Camera aria-hidden="true" /> Add photos
          </Button>
        )}
        <input ref={fileInput} type="file" accept="image/*" multiple onChange={onPick} className="hidden" aria-label="Photo files" />
        {photos.find((p) => p.error)?.error && <p className="mt-2 text-sm font-bold text-coral">{photos.find((p) => p.error)?.error}</p>}
      </div>
      <p id="form-error" role="alert" className="mt-4 min-h-6 font-bold text-coral">
        {error}
      </p>
      <Button type="submit" size="lg" className="mt-2 h-14 w-full text-lg" aria-disabled={submit.isPending || submit.isSuccess || uploading}>
        {submit.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {submit.isPending ? 'Sending…' : 'Do it for me'}
      </Button>
      <p className="mt-3 text-center text-sm text-muted-foreground">You review every answer before it counts.</p>
    </form>
  )
}
