import { expect, test } from '@playwright/test'

const username = process.env.E2E_USERNAME ?? 'e2e-admin'
const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'
const importToken = process.env.E2E_IMPORT_TOKEN

test('login, idempotent import and dashboard', async ({ page, request }) => {
  test.skip(!importToken, 'E2E_IMPORT_TOKEN must be prepared by scripts/e2e.sh')
  const payload = {
    samples: [
      { id: 'e2e-energy-1', type: 'dietary_energy_consumed', value: 1800, unit: 'kcal', start_at: '2026-07-18T12:00:00+02:00', source_identifier: 'e2e' },
      { id: 'e2e-protein-1', type: 'dietary_protein', value: 135, unit: 'g', start_at: '2026-07-18T12:10:00+02:00', source_identifier: 'e2e' },
      { id: 'e2e-carbs-1', type: 'dietary_carbohydrates', value: 190, unit: 'g', start_at: '2026-07-18T18:00:00+02:00', source_identifier: 'e2e' },
      { id: 'e2e-fat-1', type: 'dietary_fat_total', value: 58, unit: 'g', start_at: '2026-07-18T20:00:00+02:00', source_identifier: 'e2e' },
    ],
  }
  const first = await request.post('/api/v1/import/apple-health', { headers: { Authorization: `Bearer ${importToken}` }, data: payload })
  expect(first.ok()).toBeTruthy()
  expect((await first.json()).inserted).toBe(4)
  const second = await request.post('/api/v1/import/apple-health', { headers: { Authorization: `Bearer ${importToken}` }, data: payload })
  expect(second.ok()).toBeTruthy()
  expect((await second.json()).inserted).toBe(0)
  expect((await second.json()).skipped).toBe(4)

  await page.goto('/login')
  await page.getByLabel('Benutzername').fill(username)
  await page.getByLabel('Passwort').fill(password)
  await page.getByRole('button', { name: 'Anmelden' }).click()
  await expect(page.getByRole('heading', { name: 'Übersicht' })).toBeVisible()
  await page.getByRole('link', { name: /Tagesverlauf/ }).click()
  await expect(page.getByText('1.800 kcal')).toBeVisible()
})

