import { expect, test, type Page } from '@playwright/test'

// Runs against the live API with its fake solver, which answers at once. Every test sends its own
// task, so jobs already in the database do not matter.

/** Send a task from /new and wait for its result page. Returns the job's URL. */
async function sendTask(page: Page, text: string) {
  await page.goto('/new')
  await page.getByLabel('The task').fill(text)
  await page.getByRole('button', { name: 'Do it for me' }).click()
  await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
  return page.url()
}

test('sign in, send a task, and accept the answer', async ({ page }) => {
  await page.goto('/signin')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page).toHaveURL(/\/new$/)

  await sendTask(page, `Solve x^2 - 5x + 6 = 0 (${Date.now()})`)
  await expect(page.getByRole('heading', { name: 'Solution' })).toBeVisible()

  await page.getByRole('button', { name: 'Accept answer' }).click()
  await expect(page.getByText('Accepted. Done and dusted.')).toBeVisible()
  await expect(page.getByLabel('Something off? Push back')).toHaveCount(0)
})

test.describe('signed in', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('justpass.devUser', '1'))
  })

  test('feedback revises the same job', async ({ page }) => {
    const url = await sendTask(page, `Solve x^2 - 5x + 6 = 0 (${Date.now()})`)
    await page.getByLabel('Something off? Push back').fill('Explain step 2')
    await page.getByRole('button', { name: 'Get a revised answer' }).click()

    await expect(page.getByText('Revised after your feedback: “Explain step 2”')).toBeVisible()
    await expect(page.getByText('Revised answer, round 2.')).toBeVisible()
    await expect(page.getByLabel('Something off? Push back')).toHaveValue('')
    expect(page.url()).toBe(url)
  })

  test('a failed job offers to send it again, not feedback', async ({ page }) => {
    await sendTask(page, `Please fail this one (${Date.now()})`)
    await expect(page.getByRole('heading', { name: 'Could not solve this one' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Send it again' })).toHaveAttribute('href', '/new')
    await expect(page.getByLabel('Something off? Push back')).toHaveCount(0)
  })

  test('the job shows in my jobs', async ({ page }) => {
    const title = `List me (${Date.now()})`
    const url = await sendTask(page, title)
    await page.goto('/jobs')
    await page.getByRole('link', { name: title }).click()
    await expect(page).toHaveURL(url)
  })

  test('an unknown job does not exist', async ({ page }) => {
    await page.goto(`/jobs/${crypto.randomUUID()}`)
    await expect(page.getByRole('heading', { name: 'This job does not exist' })).toBeVisible()
  })
})
