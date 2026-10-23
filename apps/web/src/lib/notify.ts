import { useState } from 'react'

/** "Notify me" for features still in the lab. Local only (this browser) until a waitlist endpoint exists. */
export function useNotify(feature: string) {
  const key = `justpass.notify.${feature}`
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(key) === '1'
    } catch {
      return false
    }
  })
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
