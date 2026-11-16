import { useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { FileText, LoaderCircle, Paperclip, X } from 'lucide-react'
import { ApiError, createJob, uploadFile } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { isBadInput } from '@/lib/jobs'
import { blobToDataUrl, shrinkImage } from '@/lib/image'

const MAX_FILES = 5
const MAX_BYTES = 20 * 1024 * 1024
const TOO_BIG = 'That file is too big. Use one under 20 MB.'
/** Photos of these types are shrunk before they are uploaded. */
const SHRUNK = ['image/jpeg', 'image/png', 'image/webp']
/** Image types a thumbnail can show. */
const THUMBED = [...SHRUNK, 'image/gif']

/** A picked file: its name and size, a thumbnail for an image, and whether it is on the server (id), on its way, or failed. */
type Picked = { key: number; name: string; size: number; preview?: string; id?: string; error?: string }

function sizeLabel(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** The name with a .jpg extension, for a file that was re-encoded as a JPEG. */
function asJpeg(name: string) {
  return /\.jpe?g$/i.test(name) ? name : `${name.replace(/\.[^.]*$/, '') || 'photo'}.jpg`
}

/** Collect the task as text (prefilled when sent again), cache the job and open its result. */
export default function NewJob() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const again = (useLocation().state as { text?: string } | null)?.text
  const [text, setText] = useState(again ?? '')
  const [error, setError] = useState('')
  const [files, setFiles] = useState<Picked[]>([])
  const nextKey = useRef(0)
  const fileInput = useRef<HTMLInputElement>(null)
  const uploading = files.some((f) => !f.id && !f.error)

  const patch = (key: number, change: Partial<Picked>) => setFiles((all) => all.map((f) => (f.key === key ? { ...f, ...change } : f)))

  /** Upload each picked file at once, up to the limit; photos are shrunk first. */
  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []).slice(0, MAX_FILES - files.length)
    e.target.value = ''
    setError('')
    for (const file of picked) {
      const key = nextKey.current++
      setFiles((all) => [...all, { key, name: file.name, size: file.size }])
      void (async () => {
        let blob: Blob = file
        let name = file.name
        if (SHRUNK.includes(file.type)) {
          blob = await shrinkImage(file)
          name = asJpeg(file.name)
          patch(key, { name, size: blob.size })
        }
        if (blob.size > MAX_BYTES) throw new ApiError(TOO_BIG, 413)
        if (THUMBED.includes(blob.type)) patch(key, { preview: await blobToDataUrl(blob) })
        const stored = await uploadFile(blob, name)
        patch(key, { id: stored.id })
      })().catch((err: unknown) => patch(key, { error: err instanceof ApiError ? err.message : 'Could not add this file. Remove it and try another.' }))
    }
  }

  function remove(key: number) {
    setFiles((all) => all.filter((f) => f.key !== key))
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
    if (uploading) return setError('Wait for the files to finish uploading.')
    if (files.some((f) => f.error)) return setError('Remove the files that failed, or add them again.')
    if (!text.trim() && !files.length) return setError('Add the task as text or a file.')
    setError('')
    // Not in useMutation: this one is skipped when the student has left the page meanwhile.
    submit.mutate({ text: text.trim() ? text : null, files: files.flatMap((f) => (f.id ? [f.id] : [])) }, { onSuccess: (job) => navigate(`/jobs/${job.id}`) })
  }

  const invalid = !!error && ((!text.trim() && !files.length) || isBadInput(submit.error))
  return (
    <form onSubmit={onSubmit} noValidate className="mx-auto max-w-xl">
      <h1 className="text-6xl">What's the task?</h1>
      <p className="mt-3 text-lg text-muted-foreground">Paste it, type it, or add a photo or file.</p>
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
          {files.map((f, i) => (
            <li key={f.key} className="relative">
              {f.preview ? (
                <img src={f.preview} alt="" className={`size-24 rounded-xl border-2 object-cover ${f.error ? 'border-coral' : 'border-input'} ${f.id ? '' : 'opacity-60'}`} />
              ) : (
                <div className={`flex size-24 flex-col items-center justify-center gap-1 rounded-xl border-2 bg-card p-2 text-center ${f.error ? 'border-coral' : 'border-input'} ${f.id ? '' : 'opacity-60'}`}>
                  <FileText className="size-6 shrink-0" aria-hidden="true" />
                  <span className="w-full truncate text-xs font-bold">{f.name}</span>
                  <span className="text-xs text-muted-foreground">{sizeLabel(f.size)}</span>
                </div>
              )}
              {!f.id && !f.error && <LoaderCircle className="absolute inset-0 m-auto size-6 animate-spin" aria-label={`Uploading file ${i + 1}`} role="img" />}
              {f.error && <span role="img" aria-label={`File ${i + 1} failed`} className="absolute inset-x-0 bottom-0 rounded-b-lg bg-coral px-1 text-center text-xs font-bold text-white">Failed</span>}
              <button
                type="button"
                onClick={() => remove(f.key)}
                aria-label={`Remove file ${i + 1}`}
                className="absolute -top-2 -right-2 flex size-8 items-center justify-center rounded-full border-2 border-input bg-card"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
        {files.length < MAX_FILES && (
          <Button type="button" variant="outline" className="mt-3" onClick={() => fileInput.current?.click()}>
            <Paperclip aria-hidden="true" /> Add files
          </Button>
        )}
        <input ref={fileInput} type="file" multiple onChange={onPick} className="hidden" aria-label="Files" />
        {files.find((f) => f.error)?.error && <p className="mt-2 text-sm font-bold text-coral">{files.find((f) => f.error)?.error}</p>}
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
