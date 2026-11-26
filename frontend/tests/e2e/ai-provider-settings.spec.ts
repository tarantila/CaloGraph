import { expect, test, type Page, type Response } from '@playwright/test'

const username = process.env.E2E_USERNAME ?? 'e2e-admin'
const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'
const endpoint = 'http://ai-provider-demo:8081/v1'
const text = {
  de: { connect: 'Zugang speichern und Modelle laden', change: 'Ändern', remove: 'Entfernen', permission: 'Freigabe erteilen', revoke: 'Freigaben widerrufen', delete: 'Verbindung löschen', grant: 'Freigabe erteilen' },
  en: { connect: 'Save access and load models', change: 'Change', remove: 'Remove', permission: 'Grant permission', revoke: 'Revoke permissions', delete: 'Delete connection', grant: 'Grant permission' },
} as const
test.describe.configure({ mode: 'serial' })

async function login(page: Page, language: 'de' | 'en'): Promise<void> {
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
  await page.locator('select[name="language"]').selectOption(language)
  await page.locator('form').filter({ has: page.locator('select[name="language"]') }).getByRole('button').click()
  await expect(page.locator('html')).toHaveAttribute('lang', language)
  await page.goto('/konto/ki-anbieter')
  await expect(page.locator('select[name="ai-provider"]')).toBeVisible()
}

function connectionWrite(page: Page) {
  return page.waitForResponse(response => response.request().method() === 'PUT' && /\/settings\/ai\/connections\/[^/]+$/.test(new URL(response.url()).pathname))
}

for (const language of ['de', 'en'] as const) {
  test(`${language}: configure a native provider, grant and revoke data permission`, async ({ page }) => {
    await page.setViewportSize(language === 'de' ? { width: 1280, height: 900 } : { width: 390, height: 844 })
    await login(page, language)
    await page.locator('select[name="ai-provider"]').selectOption('local')
    await page.locator('input[name="ai-endpoint"]').fill(endpoint)
    await page.locator('input[name="ai-key"]').fill('synthetic-e2e-provider-key')
    await page.locator('input[name="ai-test-consent"]').check()

    const savedAccess = connectionWrite(page)
    const checked = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().includes('/checks/'))
    await page.getByRole('button', { name: text[language].connect, exact: true }).click()
    const connectionResponse = await savedAccess
    expect([200, 201]).toContain(connectionResponse.status())
    const saved = await connectionResponse.json() as { id: string; model_id: string | null; has_credential: boolean }
    expect(saved.model_id).toBeNull()
    expect(saved.has_credential).toBe(true)
    expect(JSON.stringify(saved)).not.toContain('synthetic-e2e-provider-key')
    expect((await checked).status()).toBe(200)

    await expect(page.locator('select[name="ai-model"] option[value="synthetic-nutrition-model"]')).toHaveCount(1)
    const modelSaved = connectionWrite(page)
    await page.locator('select[name="ai-model"]').selectOption('synthetic-nutrition-model')
    expect((await modelSaved).status()).toBe(200)

    const enabled = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/ai/routing'))
    await page.locator(`input[role="switch"][data-connection-id="${saved.id}"]`).check()
    expect((await enabled).status()).toBe(200)
    await page.getByRole('button', { name: text[language].permission, exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('checkbox').check()
    const grants = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().includes('/settings/ai/consents/'))
    await dialog.getByRole('button', { name: text[language].grant, exact: true }).click()
    expect([200, 201]).toContain((await grants).status())
    await expect(page.getByRole('button', { name: text[language].revoke, exact: true })).toBeVisible()

    const revoked: Response[] = []
    const collectRevocations = (response: Response): void => {
      if (response.request().method() === 'DELETE' && /\/settings\/ai\/consents\/[^/]+$/.test(new URL(response.url()).pathname)) revoked.push(response)
    }
    page.on('response', collectRevocations)
    await page.getByRole('button', { name: text[language].revoke, exact: true }).click()
    await expect.poll(() => revoked.length).toBe(4)
    page.off('response', collectRevocations)
    expect(new Set(revoked.map(response => response.url())).size).toBe(4)
    expect(revoked.map(response => response.status())).toEqual([204, 204, 204, 204])
    await expect(page.getByRole('button', { name: text[language].revoke, exact: true })).toHaveCount(0)

    await page.getByRole('button', { name: text[language].change, exact: true }).click()
    await expect(page.locator('input[name="ai-key"]')).toHaveValue('')
    let replacementKeyWasSubmitted = false
    page.on('request', request => {
      if (request.method() !== 'PUT' || !request.url().includes(`/settings/ai/connections/${saved.id}`)) return
      try { replacementKeyWasSubmitted ||= request.postDataJSON()?.api_key === 'synthetic-replacement-key' } catch { /* no credential in this request */ }
    })
    await page.locator('input[name="ai-key"]').fill('synthetic-replacement-key')
    await page.locator('select[name="ai-model"]').selectOption('__manual__')
    await page.locator('input[name="ai-manual-model"]').fill('synthetic-manual-model')
    expect(replacementKeyWasSubmitted).toBe(false)
    const replacement = connectionWrite(page)
    await page.locator('input[name="ai-test-consent"]').check()
    await page.getByRole('button', { name: text[language].connect, exact: true }).click()
    const replaced = await replacement
    expect(replaced.status()).toBe(200)
    expect(replaced.request().postDataJSON().api_key).toBe('synthetic-replacement-key')
    expect(replacementKeyWasSubmitted).toBe(true)
    await expect(page.locator('input[name="ai-key"]')).toHaveCount(0)
    await page.getByRole('button', { name: text[language].remove, exact: true }).click()
    const keyRemoved = connectionWrite(page)
    await page.getByRole('button', { name: language === 'de' ? 'Schlüssel entfernen und Verbindung ausschalten' : 'Remove key and disable connection', exact: true }).click()
    expect((await keyRemoved).status()).toBe(200)
    await expect(page.locator('.routing-row .routing-icon.danger')).toBeVisible()

    await page.locator('.routing-row .routing-icon.danger').click()
    await page.getByRole('dialog').getByRole('button', { name: text[language].delete, exact: true }).click()
    await expect(page.locator('.routing-row')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  })
}
