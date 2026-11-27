import { expect, test, type Page } from '@playwright/test'

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

test('Nutrition Expert conversations preserve permission, history and durable run state', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await login(page)
  await page.goto('/konto/ki-anbieter')
  const connectionId = await configureSyntheticProvider(page, 'local')
  const routing = page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/settings/ai/routing'))
  await page.locator(`input[role="switch"][data-connection-id="${connectionId}"]`).check()
  expect((await routing).ok()).toBe(true)

  const runRequests: { url: string; payload: Record<string, unknown> }[] = []
  page.on('request', request => {
    if (request.method() === 'PUT' && /\/turns\/[^/]+$/.test(new URL(request.url()).pathname)) runRequests.push({ url: request.url(), payload: request.postDataJSON() })
  })
  await page.goto('/nutrition-expert')
  await expect(page.locator('.entry-card')).toHaveCount(2)
  expect(runRequests).toHaveLength(0)
  await page.getByRole('button', { name: /Chat starten/ }).click()
  await expect(page.locator('textarea')).toBeVisible()
  expect(runRequests).toHaveLength(0)
  const chatUrl = page.url()

  await page.locator('textarea').fill('Wie ist meine Zufuhr von Ballaststoffen?')
  await page.locator('textarea').press('Enter')
  await expect(page.getByRole('dialog')).toContainText('Lokaler KI-Server')
  expect(runRequests).toHaveLength(0)
  await page.getByRole('dialog').getByRole('button', { name: 'Abbrechen', exact: true }).click()
  expect(runRequests).toHaveLength(0)
  await page.locator('textarea').press('Enter')
  await page.getByRole('dialog').getByRole('button', { name: 'Freigeben und starten', exact: true }).click()
  await expect(page.locator('.answer-text')).toHaveText(['Synthetische Testantwort zur Ernährung.'])
  await expect(page.locator('.conversation-header h2')).toHaveText('Ballaststoffzufuhr')
  expect(runRequests).toHaveLength(1)
  expect(runRequests[0]!.payload.routing_configuration_version).toBeGreaterThan(0)
  expect(runRequests[0]!.payload).not.toHaveProperty('start')
  expect(runRequests[0]!.payload).not.toHaveProperty('user_id')
  await page.reload()
  await expect(page.locator('.answer-text')).toHaveCount(1)
  expect(runRequests).toHaveLength(1)

  await page.locator('textarea').fill('synthetische Zeile')
  await page.locator('textarea').press('Shift+Enter')
  await expect(page.locator('textarea')).toHaveValue('synthetische Zeile\n')
  expect(runRequests).toHaveLength(1)
  await page.locator('textarea').fill('Bitte besprich dieses synthetische Dokument.')
  const uploaded = page.waitForRequest(request => request.method() === 'PUT' && /\/attachments\/[^/]+$/.test(new URL(request.url()).pathname))
  await page.locator('textarea').evaluate(target => {
    const transfer = new DataTransfer()
    for (let index = 0; index < 4; index++) {
      transfer.items.add(new File([`%PDF-1.7\n% Synthetic test document ${index + 1} without health data.\n%%EOF`], `synthetischer-bericht-${index + 1}.pdf`, { type: 'application/pdf' }))
    }
    for (const type of ['dragenter', 'dragover', 'drop']) {
      target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer }))
    }
  })
  expect((await uploaded).headers()['content-type']).toBe('application/pdf')
  await uploaded
  await expect(page.locator('.draft-attachments li')).toHaveCount(4)
  await expect(page.locator('.draft-attachments')).toContainText('synthetischer-bericht-4.pdf')
  await page.locator('textarea').press('Enter')
  await expect(page.getByRole('dialog')).toContainText('Anhänge')
  await page.getByRole('dialog').getByRole('button', { name: 'Freigeben und starten', exact: true }).click()
  await expect(page.locator('.answer-text')).toHaveCount(2)
  expect(runRequests).toHaveLength(2)
  expect(runRequests[1]!.payload.attachment_ids).toHaveLength(4)
  await page.reload()
  await expect(page.locator('.sent-attachments')).toContainText('synthetischer-bericht-4.pdf')
  expect(runRequests).toHaveLength(2)

  await page.getByRole('link', { name: 'Neues Gespräch', exact: true }).click()
  await page.getByRole('button', { name: /Chat starten/ }).click()
  await page.locator('textarea').fill('Eine neue Textfrage nach der Dokumentfreigabe')
  await page.locator('textarea').press('Enter')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('.answer-text')).toHaveCount(1)
  expect(runRequests).toHaveLength(3)

  await page.locator('textarea').fill('synthetic-cancel')
  await page.locator('textarea').press('Enter')
  await page.getByRole('button', { name: 'Antwort abbrechen', exact: true }).click()
  await expect(page.locator('.expert-transcript')).toContainText('Antwort abgebrochen')
  await page.reload()
  await expect(page.locator('.expert-transcript')).toContainText('Antwort abgebrochen')
  expect(runRequests).toHaveLength(4)
  expect(new Set(runRequests.map(request => request.url)).size).toBe(4)

  await page.getByRole('link', { name: 'Neues Gespräch', exact: true }).click()
  await expect(page.locator('.expert-history')).toContainText('Ballaststoffzufuhr')
  await page.getByRole('button', { name: /Analyse starten/ }).click()
  await expect(page.locator('.answer-text')).toHaveCount(1)
  await expect(page.locator('.conversation-header h2')).toContainText('Analyse -- ')
  expect(runRequests).toHaveLength(5)
  expect(runRequests[4]!.payload.kind).toBe('analysis')
  await page.getByRole('link', { name: 'Neues Gespräch', exact: true }).click()
  await expect(page.locator('.history-item')).toHaveCount(3)
  const chatId = new URL(chatUrl).searchParams.get('conversation')
  const chatRow = page.locator('.history-item').filter({ has: page.locator(`a[href*="conversation=${chatId}"]`) })
  await chatRow.getByRole('button', { name: /löschen/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Gespräch löschen', exact: true }).click()
  await expect(chatRow).toHaveCount(0)
  await expect(page.locator('.history-item')).toHaveCount(2)
  await page.goto(chatUrl)
  await expect(page.getByRole('alert')).toBeVisible()
  expect(runRequests).toHaveLength(5)
})
