import { Check, RotateCw } from 'lucide-react'
import type { Job } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** Render the decorative JustPass mark, hidden from assistive technology. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn('size-8 -rotate-6', className)}>
      <rect x="1" y="1" width="30" height="30" rx="8" fill="var(--volt)" stroke="#000" strokeWidth="2" />
      <path d="M8.5 16.5l5 5L24 10.5" fill="none" stroke="var(--ink)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

const STATUS = {
  queued: ['Queued', 'border-2 border-input text-muted-foreground'],
  running: ['Solving', 'border-2 border-volt/60 bg-volt/10 text-volt'],
  done: ['Ready', 'bg-volt text-ink'],
  failed: ['Failed', 'bg-coral text-ink'],
} as const

/** Label a job's status, giving acceptance precedence over its progress label. */
export function StatusBadge({ job, className }: { job: Job; className?: string }) {
  const [label, cls] = job.accepted ? (['Accepted', 'bg-foreground text-ink'] as const) : STATUS[job.status]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-lg px-2 py-0.5 text-sm font-bold whitespace-nowrap', cls, className)}>
      {job.status === 'running' && <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-current" />}
      {job.accepted && <Check className="size-4" aria-hidden="true" />}
      {label}
    </span>
  )
}

/** Show an error alert and invoke onRetry when the user chooses "Try again"; busy spins its icon. */
export function ErrorState({ title, onRetry, busy }: { title: string; onRetry: () => void; busy?: boolean }) {
  return (
    <div role="alert" className="rounded-2xl border-2 border-coral/70 bg-card p-5">
      <p className="font-bold text-coral">{title}</p>
      <p className="mt-1 text-muted-foreground">It could be your connection or us. Try again in a moment.</p>
      <Button variant="outline" className="mt-4" onClick={onRetry}>
        <RotateCw className={cn(busy && 'animate-spin')} aria-hidden="true" /> Try again
      </Button>
    </div>
  )
}

/** Render a pulsing loading placeholder, hidden from assistive technology. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('animate-pulse rounded-lg bg-raised', className)} />
}

/** Small pink "Soon" tag for features that are not built yet. */
export function Soon({ className }: { className?: string }) {
  return <span className={cn('rounded-md bg-pink px-1.5 text-xs leading-5 font-bold text-ink', className)}>Soon</span>
}
