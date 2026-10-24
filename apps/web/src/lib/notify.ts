import { useState } from 'react'

/**
 * "Notify me" for features still in the lab. Local only (this browser) until a waitlist endpoint exists.
 * Return the saved flag and its toggle for a stable feature key. Unreadable storage
 * starts off; failed writes leave the choice in component state for this page view.
 */
export function useNotify(feature: string) {
  const key = `justpass.notify.${feature}`
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(key) === '1'
    } catch {
      return false
    }
  })
  /** Flip the feature's preference, keeping the new state even if storage is blocked. */
  function toggle() {
    const next = !on
    setOn(next)
    try {
      if (next) localStorage.setItem(key, '1')
      else localStorage.removeItem(key)
    } catch {
      // storage blocked: the choice lasts for this page view only
    }
  }
  return [on, toggle] as const
}
