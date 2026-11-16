import { expect, type Page } from '@playwright/test'

/** The Firebase Auth emulator the web app signs in against (see playwright.config.ts). */
export const EMULATOR = 'http://127.0.0.1:9099'
const PROJECT = 'demo-justpass'

/** Sign in from /signin without an account and wait for the redirect to /new. */
export async function signInAsGuest(page: Page) {
  await page.goto('/signin')
  await page.getByRole('button', { name: 'Continue without an account' }).click()
  await expect(page).toHaveURL(/\/new$/)
}

const bearers = new WeakMap<Page, string>()

/** Remember the page's latest bearer token, to call the API outside the page as the same user. */
export function trackBearer(page: Page) {
  page.on('request', (request) => {
    const header = request.headers()['authorization']
    if (header) bearers.set(page, header)
  })
}

/** The Authorization header of the page's last API request; trackBearer(page) must be on. */
export function authorization(page: Page): Record<string, string> {
  const header = bearers.get(page)
  if (!header) throw new Error('No API request has been seen on this page yet')
  return { Authorization: header }
}

/** The newest sign-in link the emulator "sent" to an address, other than one already used. */
export async function emailedLink(page: Page, email: string, used = ''): Promise<string> {
  let link = ''
  await expect
    .poll(async () => {
      const response = await page.request.get(`${EMULATOR}/emulator/v1/projects/${PROJECT}/oobCodes`)
      const codes = ((await response.json()) as { oobCodes: { email: string; requestType: string; oobLink: string }[] }).oobCodes
      link = codes.filter((c) => c.email === email && c.requestType === 'EMAIL_SIGNIN').at(-1)?.oobLink ?? used
      return link
    })
    .not.toBe(used)
  return link
}

/** Ask for a sign-in link on /signin, open it, and wait until the app has finished signing in. */
export async function signInWithEmail(page: Page, email: string): Promise<string> {
  await page.goto('/signin')
  await page.getByLabel('Your email').fill(email)
  await page.getByRole('button', { name: 'Email me a sign-in link' }).click()
  await expect(page.getByText('Check your inbox.')).toBeVisible()
  const link = await emailedLink(page, email)
  await page.goto(link)
  await expect(page).toHaveURL(/\/new$/)
  return link
}
