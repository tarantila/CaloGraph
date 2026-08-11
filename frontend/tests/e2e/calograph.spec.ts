import { expect, test } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const username = process.env.E2E_USERNAME ?? 'e2e-admin'
const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'
const importToken = process.env.E2E_IMPORT_TOKEN

test('login, idempotent import and dashboard', async ({ page, request }) => {
  test.skip(!importToken, 'E2E_IMPORT_TOKEN must be prepared by scripts/e2e.sh')
  const runId = `e2e-${randomUUID()}`
  const payload = {
    samples: [
      { id: `${runId}-energy`, type: 'dietary_energy_consumed', value: 1800, unit: 'kcal', start_at: '2026-07-18T12:00:00+02:00', source_identifier: runId },
      { id: `${runId}-protein`, type: 'dietary_protein', value: 135, unit: 'g', start_at: '2026-07-18T12:10:00+02:00', source_identifier: runId },
      { id: `${runId}-carbs`, type: 'dietary_carbohydrates', value: 190, unit: 'g', start_at: '2026-07-18T18:00:00+02:00', source_identifier: runId },
      { id: `${runId}-fat`, type: 'dietary_fat_total', value: 58, unit: 'g', start_at: '2026-07-18T20:00:00+02:00', source_identifier: runId },
      { id: `${runId}-vitamin-d`, type: 'dietary_vitamin_d', value: 4, unit: 'ug', start_at: '2026-07-18T20:00:00+02:00', source_identifier: runId },
      { id: `${runId}-iron`, type: 'dietary_iron', value: 10, unit: 'mg', start_at: '2026-07-18T20:00:00+02:00', source_identifier: runId },
    ],
  }
  const first = await request.post('/api/v1/import/apple-health', { headers: { Authorization: `Bearer ${importToken}` }, data: payload })
  expect(first.ok()).toBeTruthy()
  expect((await first.json()).inserted).toBe(6)
  const second = await request.post('/api/v1/import/apple-health', { headers: { Authorization: `Bearer ${importToken}` }, data: payload })
  expect(second.ok()).toBeTruthy()
  expect((await second.json()).inserted).toBe(0)
  expect((await second.json()).skipped).toBe(6)

  await page.goto('/login')
  await page.getByRole('button', { name: 'Mit Passwort anmelden' }).click()
  await page.getByLabel('Benutzername').fill(username)
  await page.getByLabel('Passwort').fill(password)
  await page.getByRole('button', { name: 'Anmelden' }).click()
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await page.getByRole('link', { name: 'Wochen', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Wochenbudget', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Mikronährstoffe' }).click()
  await expect(page.getByRole('heading', { name: 'Mikronährstoffanalyse' })).toBeVisible()
  await expect(page.getByText('Vitamin D', { exact: true })).toBeVisible()
  await expect(page.getByText('Eisen', { exact: true })).toBeVisible()
})

test('administrator manages a complete synthetic account lifecycle', async ({ browser, page }) => {
  const runId = randomUUID().replaceAll('-', '').slice(0, 12)
  const lifecycleUsername = `lifecycle-${runId}`
  const initialPassword = `Initial-${runId}-account-passphrase`
  const recoveredPassword = `Recovered-${runId}-account-passphrase`

  await page.goto('/login')
  await page.getByRole('button', { name: 'Mit Passwort anmelden' }).click()
  await page.getByLabel('Benutzername').fill(username)
  await page.getByLabel('Passwort').fill(password)
  await page.getByRole('button', { name: 'Anmelden' }).click()
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await page.getByRole('link', { name: 'Konto' }).click()
  await expect(page.getByRole('heading', { name: 'Benutzerverwaltung' })).toBeVisible()

  await page.getByRole('button', { name: 'Einladungslink erzeugen' }).click()
  const invitationLink = await page.locator('.invitation-result code').innerText()

  const userContext = await browser.newContext()
  const userPage = await userContext.newPage()
  await userPage.goto(invitationLink)
  await userPage.getByLabel('Benutzername').fill(lifecycleUsername)
  await userPage.getByLabel('Passwort', { exact: true }).fill(initialPassword)
  await userPage.getByLabel('Passwort wiederholen').fill(initialPassword)
  await userPage.getByRole('button', { name: 'Konto erstellen' }).click()
  await expect(userPage).toHaveURL(/\/login\?registered=1$/)
  await userContext.close()

  await page.reload()
  let userRow = page.getByRole('row').filter({ hasText: lifecycleUsername })
  await expect(userRow).toContainText('Aktiv')
  await userRow.getByRole('button', { name: 'Deaktivieren' }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Benutzer deaktivieren' }).click()
  await expect(userRow).toContainText('Deaktiviert')

  await userRow.getByRole('button', { name: 'Authentikatoren zurücksetzen' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Dein aktuelles Admin-Passwort').fill(password)
  await dialog.getByRole('button', { name: 'Authentikatoren zurücksetzen' }).click()
  await expect(page.getByRole('status')).toContainText('Authentikatoren')

  await userRow.getByRole('button', { name: 'Recovery ausstellen' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Dein aktuelles Admin-Passwort').fill(password)
  await dialog.getByRole('button', { name: 'Recovery-Link ausstellen' }).click()
  const recoveryDialog = page.getByRole('dialog')
  const recoveryLink = await recoveryDialog.locator('code').innerText()
  expect(recoveryLink).toContain('/recovery#token=')
  await recoveryDialog.getByRole('button', { name: 'Schließen' }).click()

  const recoveryContext = await browser.newContext()
  const recoveryPage = await recoveryContext.newPage()
  await recoveryPage.goto(recoveryLink)
  await expect(recoveryPage).toHaveURL(/\/recovery$/)
  await recoveryPage.getByLabel('Neues Passwort', { exact: true }).fill(recoveredPassword)
  await recoveryPage.getByLabel('Neues Passwort wiederholen').fill(recoveredPassword)
  await recoveryPage.getByRole('button', { name: 'Passwort ändern' }).click()
  await expect(recoveryPage.getByText('Passwort wurde geändert.')).toBeVisible()
  await expect(recoveryPage.getByText('Das Konto bleibt deaktiviert.')).toBeVisible()

  await recoveryPage.getByRole('link', { name: 'Zur Anmeldung' }).click()
  await recoveryPage.getByRole('button', { name: 'Mit Passwort anmelden' }).click()
  await recoveryPage.getByLabel('Benutzername').fill(lifecycleUsername)
  await recoveryPage.getByLabel('Passwort').fill(recoveredPassword)
  await recoveryPage.getByRole('button', { name: 'Anmelden' }).click()
  await expect(recoveryPage.getByRole('alert')).toBeVisible()
  await expect(recoveryPage).toHaveURL(/\/login/)

  userRow = page.getByRole('row').filter({ hasText: lifecycleUsername })
  await userRow.getByRole('button', { name: 'Reaktivieren' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Benutzer reaktivieren' }).click()
  await expect(userRow).toContainText('Aktiv')

  await recoveryPage.getByRole('button', { name: 'Anmelden' }).click()
  await expect(recoveryPage.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await recoveryContext.close()

  await userRow.getByRole('button', { name: 'Deaktivieren' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Benutzer deaktivieren' }).click()
  await expect(userRow).toContainText('Deaktiviert')
  await userRow.getByRole('button', { name: 'Endgültig löschen' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Dein aktuelles Admin-Passwort').fill(password)
  await dialog.getByLabel(`Zur Bestätigung „${lifecycleUsername}“ eingeben`).fill(lifecycleUsername)
  await dialog.getByRole('button', { name: 'Konto endgültig löschen' }).click()
  await expect(page.getByRole('row').filter({ hasText: lifecycleUsername })).toHaveCount(0)
})
