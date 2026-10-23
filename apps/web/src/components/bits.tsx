import { Check, RotateCw } from 'lucide-react'
import type { Job } from '@/api/jobs'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn('size-7', className)}>
      <rect width="32" height="32" rx="8" fill="var(--primary)" />
      <path d="M9 16.5l4.5 4.5L23 11" fill="none" stroke="var(--primary-foreground)" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

const STATUS = {
  queued: ['Queued', 'rounded-md bg-muted text-muted-foreground'],
  running: ['Solving', 'hl bg-hl-yellow'],
  done: ['Ready', 'hl bg-hl-green'],
  failed: ['Failed', 'hl bg-hl-pink'],
} as const

export function StatusBadge({ job, className }: { job: Job; className?: string }) {
  if (job.accepted)
    return (
      <span className={cn('hl inline-flex items-center gap-1 bg-hl-green px-2 py-0.5 text-sm font-bold', className)}>
        <Check className="size-4" aria-hidden="true" /> Accepted
      </span>
    )
  const [label, cls] = STATUS[job.status]
  return (
    <span className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 text-sm font-bold whitespace-nowrap', cls, className)}>
      {job.status === 'running' && <span aria-hidden="true" className="size-2 animate-pulse rounded-full bg-current" />}
      {label}
    </span>
  )
}

export function ErrorState({ title, onRetry }: { title: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-destructive/40 bg-card p-5">
      <p className="font-bold text-destructive">{title}</p>
      <p className="mt-1 text-muted-foreground">Check your connection, then try again.</p>
      <Button variant="outline" className="mt-4" onClick={onRetry}>
        <RotateCw aria-hidden="true" /> Try again
      </Button>
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('animate-pulse rounded-lg bg-muted', className)} />
}
