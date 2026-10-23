import { useSyncExternalStore } from 'react'

export type Toast = { id: string; tone: 'done' | 'failed'; title: string; to: string }

let toasts: Toast[] = []
const listeners = new Set<() => void>()
/** Notify subscribers that the visible toast list changed. */
const emit = () => listeners.forEach((l) => l())

/**
 * Shows a toast for 10 s, longer while it is hovered or focused. The jobs list carries the same news.
 * Replace any toast with the same ID and keep only the two most recently pushed toasts.
 */
export function pushToast(t: Toast) {
  toasts = [...toasts.filter((x) => x.id !== t.id), t].slice(-2)
  emit()
  expire(t.id)
}

/** Check after 10 s and dismiss the toast, waiting another 10 s if it is hovered or contains focus. */
function expire(id: string) {
  setTimeout(() => {
    if (document.querySelector(`[data-toast="${id}"]:is(:hover, :focus-within)`)) expire(id)
    else dismissToast(id)
  }, 10_000)
}

/** Remove a toast by ID and notify subscribers, even if no toast matched. */
export function dismissToast(id: string) {
  toasts = toasts.filter((x) => x.id !== id)
  emit()
}

/** Subscribe to the current toast list, shared across the app in memory. */
export function useToasts() {
  return useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    () => toasts,
  )
}
