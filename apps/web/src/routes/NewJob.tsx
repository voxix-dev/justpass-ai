import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Camera, LoaderCircle, Trash2 } from 'lucide-react'
import { createJob } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { shrinkImage } from '@/lib/image'

export default function NewJob() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [photo, setPhoto] = useState<Blob | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState('')
  const preview = useMemo(() => (photo ? URL.createObjectURL(photo) : null), [photo])
  useEffect(() => () => void (preview && URL.revokeObjectURL(preview)), [preview])

  const submit = useMutation({
    mutationFn: createJob,
    onSuccess: (job) => {
      qc.setQueryData(['jobs', job.id], job)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
      navigate(`/jobs/${job.id}`)
    },
    onError: (e) => setError(e.message || 'Could not send your task. Try again.'),
  })

  async function pick(file: File | undefined) {
    if (!file) return
    setPreparing(true)
    setError('')
    try {
      setPhoto(await shrinkImage(file))
    } catch {
      setError('That file could not be read as a photo. Try another one.')
    } finally {
      setPreparing(false)
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!text.trim() && !photo) return setError('Add the task as text or a photo first.')
    setError('')
    submit.mutate({ text, image: photo })
  }

  const invalid = !!error && !text.trim() && !photo
  return (
    <form onSubmit={onSubmit} noValidate className="mx-auto max-w-xl">
      <h1 className="text-6xl">What's the task?</h1>
      <p className="mt-3 text-lg text-muted-foreground">Paste it, type it, or snap the page. Or do both.</p>
      <label htmlFor="question" className="mt-8 block font-bold">
        The task
      </label>
      <textarea
        id="question"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        placeholder="Solve 2x² − 8x + 6 = 0 and show each step"
        aria-describedby={error ? 'form-error' : undefined}
        aria-invalid={invalid || undefined}
        className="mt-2 block w-full resize-y rounded-2xl border-2 border-input bg-card p-4 text-lg placeholder:text-muted-foreground aria-invalid:border-coral"
      />

      <div className="mt-4">
        {preview ? (
          <figure className="flex flex-col gap-3 rounded-2xl border-2 border-border bg-card p-3 sm:flex-row sm:items-start">
            <img src={preview} alt="Your photo, ready to send" className="max-h-56 max-w-full self-start rounded-lg object-contain" />
            <Button type="button" variant="outline" onClick={() => setPhoto(null)} className="sm:ml-auto">
              <Trash2 aria-hidden="true" /> Remove photo
            </Button>
          </figure>
        ) : (
          <label className="relative flex h-24 w-full cursor-pointer items-center justify-center gap-3 rounded-2xl bg-card text-lg font-bold hover:bg-raised has-focus-visible:outline-3 has-focus-visible:outline-offset-3 has-focus-visible:outline-volt">
            {['top-2 left-2 border-t-3 border-l-3', 'top-2 right-2 border-t-3 border-r-3', 'bottom-2 left-2 border-b-3 border-l-3', 'bottom-2 right-2 border-r-3 border-b-3'].map((c) => (
              <span key={c} aria-hidden="true" className={`absolute size-5 rounded-[3px] border-volt ${c}`} />
            ))}
            {preparing ? <LoaderCircle className="size-6 animate-spin text-volt" aria-hidden="true" /> : <Camera className="size-6 text-volt" aria-hidden="true" />}
            {preparing ? 'Preparing photo…' : 'Snap or pick a photo'}
            <input
              type="file"
              accept="image/*"
              capture="environment"
              className="sr-only"
              disabled={preparing}
              onChange={(e) => {
                void pick(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </label>
        )}
      </div>

      <p id="form-error" role="alert" className="mt-4 min-h-6 font-bold text-coral">
        {error}
      </p>
      <Button type="submit" size="lg" className="mt-2 h-14 w-full text-lg" disabled={submit.isPending || preparing}>
        {submit.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {submit.isPending ? 'Sending…' : 'Do it for me'}
      </Button>
      <p className="mt-3 text-center text-sm text-muted-foreground">You review every answer before it counts.</p>
    </form>
  )
}
