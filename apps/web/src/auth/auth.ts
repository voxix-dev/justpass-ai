import { useSyncExternalStore } from 'react'
import {
  EmailAuthProvider,
  isSignInWithEmailLink,
  linkWithCredential,
  onIdTokenChanged,
  sendSignInLinkToEmail,
  signInAnonymously,
  signInWithEmailLink,
  signOut as firebaseSignOut,
  type User as FirebaseUser,
} from 'firebase/auth'
import { auth } from './firebase'

// Firebase Auth behind three entry points: screens use useAuth()/useAuthState(), and the API client
// only uses getIdToken().
export type User = { uid: string; name: string; email: string | null; isAnonymous: boolean }
export type AuthState = { user: User | null; loading: boolean }

const EMAIL_KEY = 'justpass.emailForSignIn'
const EXISTING_KEY = 'justpass.emailHasAccount'

/** Read, write (a string) or remove (null) a localStorage value; blocked storage reads as empty. */
function store(key: string, value?: string | null): string | null {
  try {
    if (value === undefined) return localStorage.getItem(key)
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // storage blocked: the page asks for the address again when the link is opened
  }
  return value ?? null
}

/** The part of a Firebase user the screens need; the display name falls back to the email, then a guest label. */
function toUser(u: FirebaseUser | null): User | null {
  if (!u) return null
  return { uid: u.uid, name: u.displayName ?? u.email ?? 'Guest', email: u.email, isAnonymous: u.isAnonymous }
}

let state: AuthState = { user: null, loading: true }
const listeners = new Set<() => void>()

/** Replace the state and notify subscribers; React only re-renders when the snapshot object changes. */
function publish(user: FirebaseUser | null, loading = false) {
  state = { user: toUser(user), loading }
  listeners.forEach((l) => l())
}

// The SDK restores the saved session asynchronously: until the first callback, loading is true. Token
// changes also cover linking an email (same uid) and sign-ins or sign-outs in other tabs.
onIdTokenChanged(auth, (u) => publish(u))

/** Start an anonymous session, so a student can try the app and keep their jobs without an account. */
export async function signInAsGuest(): Promise<void> {
  await signInAnonymously(auth)
}

/** Email a one-time sign-in link back to this page, and remember the address to finish signing in. */
export async function sendEmailLink(email: string): Promise<void> {
  await sendSignInLinkToEmail(auth, email, { url: `${window.location.origin}/signin`, handleCodeInApp: true })
  store(EMAIL_KEY, email)
}

/** Whether the current URL is an email sign-in link. */
export const isEmailLink = () => isSignInWithEmailLink(auth, window.location.href)

/** The address the link was sent to, when this browser sent it. */
export const savedEmail = () => store(EMAIL_KEY)

/**
 * Finish signing in with the link in the current URL and report what happened. An anonymous user gets the
 * email linked, so the uid and jobs stay. When that email already has an account the link is used up, so a
 * new one is sent and "existing" is returned; the next link signs in to that account.
 */
export async function completeEmailLink(email: string): Promise<'signed-in' | 'existing'> {
  const link = window.location.href
  const current = auth.currentUser
  const known = store(EXISTING_KEY) === email
  if (current?.isAnonymous && !known) {
    try {
      await linkWithCredential(current, EmailAuthProvider.credentialWithLink(email, link))
    } catch (e) {
      if (errorCode(e) !== 'auth/email-already-in-use' && errorCode(e) !== 'auth/credential-already-in-use') throw e
      store(EXISTING_KEY, email)
      await sendEmailLink(email)
      return 'existing'
    }
  } else {
    await signInWithEmailLink(auth, email, link)
  }
  store(EMAIL_KEY, null)
  store(EXISTING_KEY, null)
  return 'signed-in'
}

/** End the session. */
export async function signOut(): Promise<void> {
  await firebaseSignOut(auth)
}

/** The Firebase error code (such as "auth/invalid-email"), or an empty string for other errors. */
export function errorCode(e: unknown): string {
  return typeof e === 'object' && e && 'code' in e && typeof e.code === 'string' ? e.code : ''
}

/** Return a current ID token for the bearer header, or null when signed out; the SDK refreshes an expired one. */
export async function getIdToken(forceRefresh = false): Promise<string | null> {
  await auth.authStateReady()
  return (await auth.currentUser?.getIdToken(forceRefresh)) ?? null
}

/** Listen for changes to the signed-in user and return a function that removes the listener. */
function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The user and whether the saved session is still being restored; do not redirect to sign-in while loading. */
export function useAuthState(): AuthState {
  return useSyncExternalStore(subscribe, () => state)
}

/** The signed-in user; null while signed out or still loading. */
export function useAuth(): User | null {
  return useAuthState().user
}

