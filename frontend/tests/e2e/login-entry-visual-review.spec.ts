import { expect, test } from '@playwright/test'

test('login starts with a method selection and reveals credentials on demand', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.setViewportSize({ width: 814, height: 707 })
  await page.goto('/login')

  await expect(page.getByRole('heading', { name: 'CaloGraph' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign in with password' })).toBeVisible()
  await expect(page.locator('input')).toHaveCount(0)
  await expect(page.getByText(/Gesundheitsdaten/)).toHaveCount(0)
  await expect(page.getByText(/Registrieren/)).toHaveCount(0)

  await page.screenshot({
    path: 'test-results/login-method-selection.png',
    fullPage: true,
  })

  await page.getByRole('button', { name: 'Sign in with password' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByLabel('Username')).toBeFocused()
  await expect(page.getByLabel('Password')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Back to sign-in options' })).toBeVisible()
  await expect(page.getByText(/Registrieren/)).toHaveCount(0)

  await page.setViewportSize({ width: 743, height: 683 })
  await page.screenshot({
    path: 'test-results/login-password-form.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})

test('mobile login controls keep the zoom-preventing font invariant without page overflow', async ({ page }) => {
  const username = process.env.E2E_USERNAME ?? 'e2e-admin'
  const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/login')
  await page.getByRole('button', { name: 'Sign in with password' }).click()

  const usernameInput = page.getByLabel('Username')
  const passwordInput = page.getByLabel('Password')
  await expect(usernameInput).toBeVisible()
  await expect(passwordInput).toBeVisible()

  const fontSizes = await Promise.all(
    [usernameInput, passwordInput].map((input) =>
      input.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
    ),
  )
  expect(fontSizes[0]).toBeGreaterThanOrEqual(16)
  expect(fontSizes[1]).toBeGreaterThanOrEqual(16)

  await usernameInput.fill(username)
  await passwordInput.fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).not.toHaveURL(/\/login/)

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth)
})
