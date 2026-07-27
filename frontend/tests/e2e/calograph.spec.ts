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
