import { useSyncExternalStore } from 'react'

// Dev stub: a fake signed-in user kept in localStorage. Replace the inside of this module with
// Firebase Auth later (signInWithPopup, onIdTokenChanged, user.getIdToken() for the bearer
// header); screens only use useAuth() and the API client only uses getIdToken().
export type User = { uid: string; name: string; email: string }

const KEY = 'justpass.devUser'
const DEV_USER: User = { uid: 'dev-user', name: 'Alex Student', email: 'alex@example.com' }

let current: User | null = (() => {
  try {
    return localStorage.getItem(KEY) ? DEV_USER : null
  } catch {
    return null
  }
})()
const listeners = new Set<() => void>()

function set(user: User | null) {
  current = user
  try {
    if (user) localStorage.setItem(KEY, '1')
    else localStorage.removeItem(KEY)
  } catch {
    // storage blocked: stay signed in for this tab only
  }
  listeners.forEach((l) => l())
}

export async function signIn(): Promise<void> {
  await new Promise((r) => setTimeout(r, 600))
  set(DEV_USER)
}

export function signOut(): void {
  set(null)
}

export async function getIdToken(): Promise<string | null> {
  return current ? 'dev-token' : null
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useAuth(): User | null {
  return useSyncExternalStore(subscribe, () => current)
}
