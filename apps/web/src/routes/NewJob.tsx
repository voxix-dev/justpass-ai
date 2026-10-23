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
    onError: (e) => setError(e.message || 'Could not send your question. Try again.'),
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
    if (!text.trim() && !photo) return setError('Type a question or add a photo first.')
    setError('')
    submit.mutate({ text, image: photo })
  }

  const invalid = !!error && !text.trim() && !photo
  return (
    <form onSubmit={onSubmit} noValidate className="mx-auto max-w-xl">
      <h1 className="text-3xl sm:text-4xl">Ask a question</h1>
      <label htmlFor="question" className="mt-8 block font-bold">
        Your question
      </label>
      <p id="question-hint" className="text-sm text-muted-foreground">
        Type it out, add a photo of the page, or both.
      </p>
      <textarea
        id="question"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        placeholder="Solve x² − 5x + 6 = 0 and show each step"
        aria-describedby={error ? 'question-hint form-error' : 'question-hint'}
        aria-invalid={invalid || undefined}
        className="mt-2 block w-full resize-y rounded-xl border-2 border-input bg-card p-3 text-base placeholder:text-muted-foreground aria-invalid:border-destructive"
      />

      <div className="mt-4">
        {preview ? (
          <figure className="flex items-start gap-3 rounded-xl border bg-card p-3">
            <img src={preview} alt="Your photo, ready to send" className="max-h-48 w-auto max-w-[60%] rounded-lg object-contain" />
            <Button type="button" variant="outline" onClick={() => setPhoto(null)} className="ml-auto">
              <Trash2 aria-hidden="true" /> Remove photo
            </Button>
          </figure>
        ) : (
          <label className="flex h-14 w-full cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed border-input font-bold text-ink hover:bg-muted has-focus-visible:outline-3 has-focus-visible:outline-ring">
            {preparing ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" /> : <Camera className="size-5" aria-hidden="true" />}
            {preparing ? 'Preparing photo…' : 'Add a photo'}
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

      <p id="form-error" role="alert" className="mt-4 min-h-6 font-semibold text-destructive">
        {error}
      </p>
      <Button type="submit" size="lg" className="mt-2 w-full sm:w-auto" disabled={submit.isPending || preparing}>
        {submit.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {submit.isPending ? 'Sending…' : 'Get the solution'}
      </Button>
    </form>
  )
}
