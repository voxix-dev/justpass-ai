import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { LoaderCircle } from 'lucide-react'
import { createJob } from '@/api/jobs'
import { Button } from '@/components/ui/button'

/** Collect the task as text, then cache the submitted job and navigate to its result. */
export default function NewJob() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  const submit = useMutation({
    mutationFn: createJob,
    onSuccess: (job) => {
      qc.setQueryData(['jobs', job.id], job)
      void qc.invalidateQueries({ queryKey: ['jobs'], exact: true })
      navigate(`/jobs/${job.id}`)
    },
    onError: (e) => setError(e.message || 'Could not send your task. Try again.'),
  })

  /** Prevent native submission and submit only when the task has text. */
  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!text.trim()) return setError('Add the task first.')
    setError('')
    submit.mutate({ text })
  }

  const invalid = !!error && !text.trim()
  return (
    <form onSubmit={onSubmit} noValidate className="mx-auto max-w-xl">
      <h1 className="text-6xl">What's the task?</h1>
      <p className="mt-3 text-lg text-muted-foreground">Paste it or type it.</p>
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
      <p id="form-error" role="alert" className="mt-4 min-h-6 font-bold text-coral">
        {error}
      </p>
      <Button type="submit" size="lg" className="mt-2 h-14 w-full text-lg" disabled={submit.isPending}>
        {submit.isPending && <LoaderCircle className="animate-spin" aria-hidden="true" />}
        {submit.isPending ? 'Sending…' : 'Do it for me'}
      </Button>
      <p className="mt-3 text-center text-sm text-muted-foreground">You review every answer before it counts.</p>
    </form>
  )
}
