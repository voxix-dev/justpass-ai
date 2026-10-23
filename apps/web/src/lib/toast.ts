import { useSyncExternalStore } from 'react'

export type Toast = { id: string; tone: 'done' | 'failed'; title: string; to: string }

let toasts: Toast[] = []
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())

/** Shows a toast for 10 s, longer while it is hovered or focused. The jobs list carries the same news. */
export function pushToast(t: Toast) {
  toasts = [...toasts.filter((x) => x.id !== t.id), t].slice(-2)
  emit()
  expire(t.id)
}

function expire(id: string) {
  setTimeout(() => {
    if (document.querySelector(`[data-toast="${id}"]:is(:hover, :focus-within)`)) expire(id)
    else dismissToast(id)
  }, 10_000)
}

export function dismissToast(id: string) {
  toasts = toasts.filter((x) => x.id !== id)
  emit()
}

export function useToasts() {
  return useSyncExternalStore(
    (l) => (listeners.add(l), () => listeners.delete(l)),
    () => toasts,
  )
}
