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
    await expect(page.locator('#answer')).toBeFocused()
    await expect(page.getByText('Revised after your feedback')).toBeInViewport()
    expect(page.url()).toBe(url)

    await page.waitForTimeout(1100) // past the double-click window
    await page.getByRole('button', { name: 'Get a revised answer' }).click()
    await expect(page.getByText('Write what should change first.')).toBeVisible()
  })

  test('math is typeset with the CSS of the same KaTeX version', async ({ page }) => {
    await sendTask(page, `Solve x^2 - 5x + 6 = 0 (${Date.now()})`)
    await expect(page.getByRole('heading', { name: 'Solution' })).toBeVisible()
    // The first formula is x^2 - ..., so its first "2" is an exponent. Class names change between
    // KaTeX versions, and that is how the mismatch went unnoticed: compare what the browser computed.
    const [exponent, base] = await page.evaluate<[number, number]>(`(() => {
      const math = document.querySelector('.katex-html')
      const two = [...math.querySelectorAll('*')].find((e) => !e.children.length && e.textContent === '2')
      return [two, math].map((e) => parseFloat(getComputedStyle(e).fontSize))
    })()`)
    expect(exponent).toBeLessThan(base * 0.8)
  })

  test('a double tap on send does not accept the answer', async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 740 })
    await page.goto('/new')
    await page.getByLabel('The task').fill(`Solve 2x² − 8x + 6 = 0 and show each step (${Date.now()})`)
    const box = (await page.getByRole('button', { name: 'Do it for me' }).boundingBox())!
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await page.waitForTimeout(180)
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]{36}$/)
    await expect(page.getByRole('button', { name: 'Accept answer' })).toBeEnabled()
    await expect(page.getByText('Accepted. Done and dusted.')).toHaveCount(0)
  })

  test('an answer read in a stale tab cannot be accepted', async ({ page, context }) => {
    const url = await sendTask(page, `Solve x^2 - 5x + 6 = 0 (${Date.now()})`)
    const stale = await context.newPage()
    await stale.goto(url)
    await expect(stale.getByRole('button', { name: 'Accept answer' })).toBeEnabled()

    await page.getByLabel('Something off? Push back').fill('More detail')
    await page.getByRole('button', { name: 'Get a revised answer' }).click()
    await expect(page.getByText('Revised answer, round 2.')).toBeVisible()

    await stale.getByRole('button', { name: 'Accept answer' }).click()
    await expect(stale.getByText('Revised answer, round 2.')).toBeVisible()
    await expect(stale.getByText('Accepted. Done and dusted.')).toHaveCount(0)
  })

  test('a lost connection keeps the answer and the typed feedback', async ({ page }) => {
    await sendTask(page, `Solve x^2 - 5x + 6 = 0 (${Date.now()})`)
    await page.getByLabel('Something off? Push back').fill('Keep this text')
    await page.route('**/api/**', (route) => route.abort())
    await page.getByRole('button', { name: 'Get a revised answer' }).click()

    await expect(page.getByText('Could not reach JustPass')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Solution' })).toBeVisible()
    await expect(page.getByLabel('Something off? Push back')).toHaveValue('Keep this text')
  })

  test('a failed task can be sent again without retyping it', async ({ page }) => {
    const text = `Please fail this one (${Date.now()})`
    await sendTask(page, text)
    await page.getByRole('link', { name: 'Send it again' }).click()
    await expect(page.getByLabel('The task')).toHaveValue(text)
  })

  test.describe('a hostile answer', () => {
    /** Answer the job's reads with this Markdown, as a real solver could. */
    async function showAnswer(page: Page, result: string) {
      const url = await sendTask(page, `Hostile answer (${Date.now()})`)
      await page.route('**/api/jobs/*', async (route) => {
        const response = await route.fetch()
        await route.fulfill({ response, json: { ...(await response.json()), result } })
      })
      await page.goto(url)
    }

    test('loads no images and shows where links go', async ({ page }) => {
      const external: string[] = []
      page.on('request', (r) => void (r.url().includes('evil.example') && external.push(r.url())))
      await showAnswer(page, '![grade D](http://evil.example/p.png?grade=D)\n\n[Verify your account](http://evil.example/login)')

      await expect(page.getByRole('link', { name: 'Verify your account (evil.example)' })).toBeVisible()
      await expect(page.getByText('grade D')).toBeVisible()
      expect(external).toEqual([])
    })

    test('that cannot render keeps the review actions', async ({ page }) => {
      await showAnswer(page, `x\n\n${'- '.repeat(1000)}x`)
      await expect(page.getByRole('button', { name: 'Accept answer' })).toBeVisible()
      await expect(page.getByText('This page did not load.')).toHaveCount(0)
    })

    test('stays inside a phone screen', async ({ page }) => {
      await page.setViewportSize({ width: 360, height: 740 })
      await showAnswer(page, `See ${'long'.repeat(100)} and $\\rule{10000em}{10000em}$`)
      await expect(page.locator('.answer')).toBeVisible()
      expect(await page.evaluate<number>('document.documentElement.scrollWidth')).toBeLessThanOrEqual(360)
    })
  })

  test('signing out goes home and closes the job', async ({ page }) => {
    await sendTask(page, `Private (${Date.now()})`)
    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('link', { name: 'Sign in' })).toBeVisible()
    await expect(page).toHaveTitle('JustPass')
  })

  test('every page has its own title', async ({ page }) => {
    await page.goto('/jobs')
    await expect(page).toHaveTitle('My jobs – JustPass')
    await page.goto('/new')
    await expect(page).toHaveTitle('New task – JustPass')
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
