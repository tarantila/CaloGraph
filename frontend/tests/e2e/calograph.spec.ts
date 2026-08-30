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
  await page.getByRole('button', { name: 'Sign in with password' }).click()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(
    page.getByRole('heading', { name: /^(Willkommen bei CaloGraph|Welcome to CaloGraph|Ernährungsüberblick|Nutrition overview)$/ }),
  ).toBeVisible()
  const setupHeading = page.getByRole('heading', { name: /^(Willkommen bei CaloGraph|Welcome to CaloGraph)$/ })
  if (await setupHeading.isVisible()) {
    await expect(page).toHaveURL(/\/einrichtung$/)
    await expect(page.getByLabel(/^(Kalorienbudget pro Tag|Daily calorie budget)(?: kcal)?$/)).toHaveValue('')
    await expect(page.getByLabel(/^(Proteinziel pro Tag|Daily protein target)(?: g)?$/)).toHaveValue('')
    await expect(page.getByRole('navigation')).toHaveCount(0)
    await page.getByLabel(/^(Kalorienbudget pro Tag|Daily calorie budget)(?: kcal)?$/).fill('2100')
    await page.getByLabel(/^(Proteinziel pro Tag|Daily protein target)(?: g)?$/).fill('135')
    await page.getByRole('button', { name: /^(Einrichtung abschließen|Complete setup)$/ }).click()
  }
  if (await page.getByRole('heading', { name: 'Nutrition overview' }).isVisible()) {
    await page.goto('/konto/allgemeine-einstellungen')
    await expect(page.locator('select[name="language"]')).toBeVisible()
    await page.locator('select[name="language"]').selectOption('de')
    await page.locator('form:has(select[name="language"]) button[type="submit"]').click()
  }
  await page.getByRole('link', { name: /^(Übersicht|Overview)$/ }).click()
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await page.getByRole('link', { name: 'Wochenbudget', exact: true }).click()
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
  await page.getByRole('button', { name: 'Sign in with password' }).click()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: /^(Ernährungsüberblick|Nutrition overview|Overview)$/ })).toBeVisible()
  await page.goto('/konto/allgemeine-einstellungen')
  await expect(page.locator('select[name="language"]')).toBeVisible()
  await page.locator('select[name="language"]').selectOption('de')
  await page.locator('form:has(select[name="language"]) button[type="submit"]').click()
  await expect(page.getByRole('heading', { name: 'Allgemeine Einstellungen' })).toBeVisible()
  await page.goto('/admin/invitations')
  await expect(page.getByRole('heading', { name: 'Einladungen' }).first()).toBeVisible()
  await page.getByRole('button', { name: 'Einladung erstellen' }).click()
  const invitationLink = await page.locator('.invitation-result code').innerText()

  const userContext = await browser.newContext()
  const userPage = await userContext.newPage()
  await userPage.goto(invitationLink)
  await userPage.getByLabel('Username').fill(lifecycleUsername)
  await userPage.getByLabel('Password', { exact: true }).fill(initialPassword)
  await userPage.getByLabel('Repeat password').fill(initialPassword)
  await userPage.getByRole('button', { name: 'Create account' }).click()
  await expect(userPage).toHaveURL(/\/login\?registered=1$/)
  await userPage.getByLabel('Username').fill(lifecycleUsername)
  await userPage.getByLabel('Password').fill(initialPassword)
  await userPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(userPage).toHaveURL(/\/einrichtung$/)
  await expect(userPage.getByRole('heading', { name: 'Willkommen bei CaloGraph' })).toBeVisible()
  await expect(userPage.getByText('Bevor es losgeht, benötigen wir zwei Werte von dir.')).toBeVisible()
  await expect(userPage.getByRole('navigation')).toHaveCount(0)
  await expect(userPage.getByLabel(/^Kalorienbudget pro Tag(?: kcal)?$/)).toHaveValue('')
  await expect(userPage.getByLabel(/^Proteinziel pro Tag(?: g)?$/)).toHaveValue('')
  await expect(userPage.getByText('2200', { exact: true })).toHaveCount(0)
  await expect(userPage.getByText('140', { exact: true })).toHaveCount(0)
  await userPage.getByRole('group').getByText('Weitere Ziele festlegen').click()
  await userPage.getByLabel(/^Kalorienbudget pro Tag(?: kcal)?$/).fill('3000')
  await userPage.getByLabel('Erhaltungsbedarf', { exact: false }).fill('2500')
  await userPage.getByLabel(/^Proteinziel pro Tag(?: g)?$/).fill('135')
  await userPage.getByRole('button', { name: 'Einrichtung abschließen' }).click()
  await expect(userPage).toHaveURL(/\/$/)
  await expect(userPage.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await userPage.getByRole('link', { name: 'Wochenbudget', exact: true }).click()
  await expect(userPage.getByRole('heading', { name: 'Wochenbudget', exact: true })).toBeVisible()
  await userPage.getByRole('button', { name: 'Abmelden' }).click()
  await expect(userPage).toHaveURL(/\/login$/)
  await userPage.getByRole('button', { name: 'Sign in with password' }).click()
  await userPage.getByLabel('Username').fill(lifecycleUsername)
  await userPage.getByLabel('Password').fill(initialPassword)
  await userPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(userPage).toHaveURL(/\/$/)
  await expect(userPage.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await userPage.getByRole('button', { name: 'Abmelden' }).click()
  await expect(userPage).toHaveURL(/\/login$/)
  await userContext.close()

  await page.goto('/admin/users')
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
  await recoveryPage.getByLabel('New password', { exact: true }).fill(recoveredPassword)
  await recoveryPage.getByLabel('Repeat new password').fill(recoveredPassword)
  await recoveryPage.getByRole('button', { name: 'Change password' }).click()
  await expect(recoveryPage.getByText('Password changed.')).toBeVisible()
  await expect(recoveryPage.getByText('The account remains disabled.')).toBeVisible()

  await recoveryPage.getByRole('link', { name: 'Back to sign in' }).click()
  await recoveryPage.getByRole('button', { name: 'Sign in with password' }).click()
  await recoveryPage.getByLabel('Username').fill(lifecycleUsername)
  await recoveryPage.getByLabel('Password').fill(recoveredPassword)
  await recoveryPage.getByRole('button', { name: 'Sign in' }).click()
  await expect(recoveryPage.getByRole('alert')).toBeVisible()
  await expect(recoveryPage).toHaveURL(/\/login/)

  userRow = page.getByRole('row').filter({ hasText: lifecycleUsername })
  await userRow.getByRole('button', { name: 'Reaktivieren' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Benutzer reaktivieren' }).click()
  await expect(userRow).toContainText('Aktiv')

  await recoveryPage.getByRole('button', { name: 'Sign in' }).click()
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
