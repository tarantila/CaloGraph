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

async function configureSyntheticProvider(page: Page, kind: 'local' | 'gateway'): Promise<string> {
  await page.locator('select[name="ai-provider"]').selectOption(kind)
  await page.locator('input[name="ai-endpoint"]').fill(endpoint)
  await page.locator('input[name="ai-key"]').fill('synthetic-e2e-provider-key')
  await page.locator('input[name="ai-test-consent"]').check()
  const response = page.waitForResponse(result => result.request().method() === 'PUT' && /\/settings\/ai\/connections\/[^/]+$/.test(new URL(result.url()).pathname))
  await page.getByRole('button', { name: 'Zugang speichern und Modelle laden', exact: true }).click()
  const saved = await response
  expect([200, 201]).toContain(saved.status())
  const connection = await saved.json() as { id: string }
  await expect(page.locator('select[name="ai-model"] option[value="synthetic-nutrition-model"]')).toHaveCount(1)
  const modelSaved = page.waitForResponse(result => result.request().method() === 'PUT' && result.url().includes(`/settings/ai/connections/${connection.id}`))
  await page.locator('select[name="ai-model"]').selectOption('synthetic-nutrition-model')
  expect((await modelSaved).status()).toBe(200)
  return connection.id
}

async function setRoutingEnabled(page: Page, id: string, enabled: boolean): Promise<void> {
  const request = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/ai/routing'))
  const control = page.locator(`input[role="switch"][data-connection-id="${id}"]`)
  if (enabled) await control.check()
  else await control.uncheck()
  expect((await request).status()).toBe(200)
}

async function grantAllNutritionScopes(page: Page, connectionId: string): Promise<Response[]> {
  const responses: Response[] = []
  const onResponse = (response: Response): void => {
    if (response.request().method() === 'PUT' && /\/settings\/ai\/consents\/[^/]+$/.test(new URL(response.url()).pathname)) responses.push(response)
  }
  page.on('response', onResponse)
  try {
    await page.locator(`.consent-row[data-connection-id="${connectionId}"]`).getByRole('button', { name: 'Freigabe erteilen', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('checkbox').check()
    await dialog.getByRole('button', { name: 'Freigabe erteilen', exact: true }).click()
    await expect(dialog).toBeHidden()
    await expect.poll(() => responses.length).toBe(4)
    expect(responses.every(response => [200, 201].includes(response.status()))).toBe(true)
    expect(new Set(responses.map(response => response.url())).size).toBe(4)
    return [...responses]
  } finally {
    page.off('response', onResponse)
  }
}

test('Nutrition Expert can revoke all account permissions with one action', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await login(page)
  await page.goto('/konto/ki-anbieter')

  const localId = await configureSyntheticProvider(page, 'local')
  const gatewayId = await configureSyntheticProvider(page, 'gateway')
  await setRoutingEnabled(page, localId, true)
  await setRoutingEnabled(page, gatewayId, true)
  await expect(page.locator('.routing-row')).toHaveCount(2)
  const localRow = page.locator('.routing-row').filter({ has: page.locator(`input[data-connection-id="${localId}"]`) })
  if ((await localRow.locator('.routing-position').innerText()).trim() !== '1.') {
    const reordered = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/ai/routing'))
    await localRow.getByRole('button', { name: 'Lokaler KI-Server nach oben', exact: true }).click()
    expect((await reordered).status()).toBe(200)
  }
  await expect(localRow).toContainText('Bevorzugter Anbieter')
  await expect(page.locator('.routing-row').filter({ has: page.locator(`input[data-connection-id="${gatewayId}"]`) })).toContainText('Ersatzanbieter')

  const grantedResponses = [
    ...await grantAllNutritionScopes(page, localId),
    ...await grantAllNutritionScopes(page, gatewayId),
  ]
  await setRoutingEnabled(page, gatewayId, false)

  await page.goto('/konto/nutrition-expert')
  await expect(page.getByRole('heading', { name: 'Daten für den Nutrition Expert' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'KI-Einwilligung' })).toBeVisible()
  const permissionCard = page.locator('.expert-consent-card')
  await expect(permissionCard.getByRole('button', { name: 'KI-Einwilligung widerrufen', exact: true })).toBeVisible()
  await expect(permissionCard).not.toContainText('OpenAI-kompatibler Anbieter')
  await expect(permissionCard).not.toContainText('synthetic-nutrition-model')
  await expect(permissionCard).not.toContainText(endpoint)

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
  await expect(permissionCard.getByRole('button', { name: 'KI-Einwilligung widerrufen', exact: true })).toBeVisible()

  const deletedConsents: Response[] = []
  const collectDeletes = (response: Response): void => {
    if (response.request().method() === 'DELETE' && /\/settings\/ai\/consents\/[^/]+$/.test(new URL(response.url()).pathname)) deletedConsents.push(response)
  }
  page.on('response', collectDeletes)
  await permissionCard.getByRole('button', { name: 'KI-Einwilligung widerrufen', exact: true }).click()
  await expect.poll(() => deletedConsents.length).toBe(8)
  await expect(page.getByText('Alle KI-Einwilligungen wurden widerrufen. Neue Anfragen benötigen deine Zustimmung.')).toBeVisible()
  page.off('response', collectDeletes)
  expect(new Set(deletedConsents.map(response => response.url())).size).toBe(8)
  expect(deletedConsents.every(response => response.status() === 204)).toBe(true)
  expect(grantedResponses.length).toBe(8)
  await expect(permissionCard.getByRole('button', { name: 'KI-Einwilligung widerrufen', exact: true })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.reload()
  await expect(page.getByText('Es sind keine aktiven Datenfreigaben vorhanden.')).toBeVisible()
  await expect(page.locator('.expert-consent-card').getByRole('button')).toHaveCount(0)

  await page.goto('/konto/ki-anbieter')
  for (const [id, label] of [[gatewayId, 'OpenAI-kompatibler Anbieter'], [localId, 'Lokaler KI-Server']] as const) {
    const row = page.locator('.routing-row').filter({ has: page.locator(`input[data-connection-id="${id}"]`) })
    await row.getByRole('button', { name: `${label} löschen`, exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Verbindung löschen', exact: true }).click()
    await expect(row).toHaveCount(0)
  }
})
