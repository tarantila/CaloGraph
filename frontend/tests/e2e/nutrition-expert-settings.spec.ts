import { expect, test, type Page, type Response } from '@playwright/test'

const username = process.env.E2E_USERNAME ?? 'e2e-admin'
const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'
const endpoint = 'http://ai-provider-demo:8081/v1'

async function login(page: Page): Promise<void> {
  await page.goto('/login')
  await page.getByRole('button', { name: /^(Sign in with password|Mit Passwort anmelden)$/ }).click()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.getByRole('button', { name: /^(Sign in|Anmelden)$/ }).click()
  await expect(page).toHaveURL(url => ['/', '/einrichtung'].includes(url.pathname))
  if (new URL(page.url()).pathname === '/einrichtung') {
    await page.locator('input[name="calories-kcal"]').fill('2100')
    await page.locator('input[name="protein-g"]').fill('135')
    await page.locator('form.setup-form button[type="submit"]').click()
  }
  await page.goto('/konto/allgemeine-einstellungen')
  await page.locator('select[name="language"]').selectOption('de')
  await page.locator('form').filter({ has: page.locator('select[name="language"]') }).getByRole('button').click()
  await expect(page.locator('html')).toHaveAttribute('lang', 'de')
}

test('Nutrition Expert preferences auto-save without provider requests and can revoke all permissions', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await login(page)
  await page.goto('/konto/ki-anbieter')
  await page.locator('select[name="ai-provider"]').selectOption('local')
  await page.locator('input[name="ai-endpoint"]').fill(endpoint)
  await page.locator('input[name="ai-key"]').fill('synthetic-e2e-provider-key')
  await page.locator('input[name="ai-test-consent"]').check()

  const savedConnection = page.waitForResponse(response => response.request().method() === 'PUT' && /\/settings\/ai\/connections\/[^/]+$/.test(new URL(response.url()).pathname))
  await page.getByRole('button', { name: 'Zugang speichern und Modelle laden', exact: true }).click()
  expect([200, 201]).toContain((await savedConnection).status())
  await expect(page.locator('select[name="ai-model"] option[value="synthetic-nutrition-model"]')).toHaveCount(1)
  const modelSaved = page.waitForResponse(response => response.request().method() === 'PUT' && /\/settings\/ai\/connections\/[^/]+$/.test(new URL(response.url()).pathname))
  await page.locator('select[name="ai-model"]').selectOption('synthetic-nutrition-model')
  expect((await modelSaved).status()).toBe(200)
  const saved = await page.locator('.routing-row input[role="switch"]').first().getAttribute('data-connection-id')
  expect(saved).toBeTruthy()
  const enabled = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/ai/routing'))
  await page.locator(`input[role="switch"][data-connection-id="${saved}"]`).check()
  expect((await enabled).status()).toBe(200)
  await page.getByRole('button', { name: 'Freigabe erteilen', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('checkbox').check()
  const grantRequest = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().includes('/settings/ai/consents/'))
  await dialog.getByRole('button', { name: 'Freigabe erteilen', exact: true }).click()
  expect([200, 201]).toContain((await grantRequest).status())
  await expect(dialog).toBeHidden()

  await page.goto('/konto/nutrition-expert')
  await expect(page.getByRole('heading', { name: 'Daten für den Nutrition Expert' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Datenfreigabe' })).toBeVisible()
  const outboundRequests: string[] = []
  page.on('request', request => {
    if (request.url().includes('/expert/runs') || request.url().includes('/checks/')) outboundRequests.push(request.url())
  })
  const savedPreference = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/expert'))
  await page.getByRole('switch', { name: 'Aktivität' }).uncheck()
  const preferenceResponse = await savedPreference
  expect(preferenceResponse.status()).toBe(200)
  expect(await preferenceResponse.request().postDataJSON()).toMatchObject({ include_meal_names: true })
  await expect(page.getByRole('status').filter({ hasText: 'Einstellungen gespeichert' })).toBeVisible()
  expect(outboundRequests).toEqual([])
  await page.reload()
  await expect(page.getByRole('switch', { name: 'Aktivität' })).not.toBeChecked()

  const deletedConsents: Response[] = []
  const collectDeletes = (response: Response): void => {
    if (response.request().method() === 'DELETE' && /\/settings\/ai\/consents\/[^/]+$/.test(new URL(response.url()).pathname)) deletedConsents.push(response)
  }
  page.on('response', collectDeletes)
  await page.getByRole('button', { name: 'Freigaben für diesen Anbieter widerrufen', exact: true }).click()
  await expect.poll(() => deletedConsents.length).toBe(4)
  await expect(page.getByText('Freigaben widerrufen. Neue Anfragen benötigen deine Zustimmung.')).toBeVisible()
  page.off('response', collectDeletes)
  expect(new Set(deletedConsents.map(response => response.url())).size).toBe(4)
  expect(deletedConsents.every(response => response.status() === 204)).toBe(true)
  await expect(page.getByRole('button', { name: 'Freigaben für diesen Anbieter widerrufen', exact: true })).toHaveCount(0)
  await expect(page.getByRole('switch', { name: 'Aktivität' })).not.toBeChecked()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.goto('/konto/ki-anbieter')
  await page.locator('.routing-row button[aria-label*="Lokaler KI-Server löschen"]').click()
  await page.getByRole('dialog').getByRole('button', { name: 'Verbindung löschen', exact: true }).click()
  await expect(page.locator('.routing-row')).toHaveCount(0)
})
