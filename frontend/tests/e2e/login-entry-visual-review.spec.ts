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
  await expect(page.getByRole('button', { name: 'Mit Passwort anmelden' })).toBeVisible()
  await expect(page.locator('input')).toHaveCount(0)
  await expect(page.getByText(/Gesundheitsdaten/)).toHaveCount(0)
  await expect(page.getByText(/Registrieren/)).toHaveCount(0)

  await page.screenshot({
    path: 'test-results/login-method-selection.png',
    fullPage: true,
  })

  await page.getByRole('button', { name: 'Mit Passwort anmelden' }).click()
  await expect(page.getByRole('heading', { name: 'Anmelden' })).toBeVisible()
  await expect(page.getByLabel('Benutzername')).toBeFocused()
  await expect(page.getByLabel('Passwort')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Zurück zur Anmeldeauswahl' })).toBeVisible()
  await expect(page.getByText(/Registrieren/)).toHaveCount(0)

  await page.setViewportSize({ width: 743, height: 683 })
  await page.screenshot({
    path: 'test-results/login-password-form.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})
