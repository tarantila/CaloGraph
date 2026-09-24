import { expect, test, type Page } from '@playwright/test'

test.use({ locale: 'de-DE', timezoneId: 'Europe/Berlin' })

type NutritionResponse = {
  canonical: { provider_key: string; record_count: number; events: Array<Record<string, unknown>> } | null
  providers: Array<{ provider_key: string; status: string; record_count: number; events: Array<Record<string, unknown>> }>
}

type ActivityResponse = {
  days: Array<{
    date: string
    canonical: { provider_key: string; status: string; active_energy_kcal: number | null; record_count: number } | null
    providers: Array<{ provider_key: string; status: string; active_energy_kcal: number | null; record_count: number }>
  }>
}

const username = process.env.E2E_VERIFICATION_USERNAME ?? 'verification-e2e-user'
const otherUsername = process.env.E2E_VERIFICATION_OTHER_USERNAME ?? 'verification-e2e-other-user'
const password = process.env.E2E_VERIFICATION_PASSWORD ?? 'verification-e2e-local-passphrase'
function verificationDate(): string {
  const date = process.env.E2E_VERIFICATION_DATE
  if (!date) {
    throw new Error('E2E_VERIFICATION_DATE is required for Verification E2E')
  }
  return date
}

function verificationInstant(): Date {
  return new Date(`${verificationDate()}T12:00:00Z`)
}

test.beforeEach(async ({ page }) => {
  const target = verificationInstant()
  await page.clock.install({
    time: new Date(target.getTime() - 60_000),
  })
})

async function login(
  page: Page,
  loginUsername = username,
  loginPassword = password,
): Promise<void> {
  await page.goto('/login')
  await page.getByRole('button', { name: 'Mit Passwort anmelden' }).click()
  await page.getByLabel('Benutzername').fill(loginUsername)
  await page.getByLabel('Passwort').fill(loginPassword)
  await page.getByRole('button', { name: 'Anmelden' }).click()
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await page.clock.pauseAt(verificationInstant())
}

function eventByName(events: Array<Record<string, unknown>>, name: string): Record<string, unknown> {
  const event = events.find((item) => item.display_name === name)
  expect(event, `expected synthetic event ${name}`).toBeDefined()
  return event!
}

test('reads isolated nutrition evidence without provider blending', async ({ page }) => {
  await login(page)
  const responsePromise = page.waitForResponse((response) => response.url().includes('/analytics/verification/nutrition') && response.status() === 200)
  await page.goto('/ernaehrung')
  const response = await responsePromise
  const body = await response.json() as NutritionResponse
  expect(body.canonical?.provider_key).toBe('google_health')
  expect(body.canonical?.record_count).toBe(6)
  expect(body.canonical?.events).toHaveLength(6)
  expect(response.url()).toContain(`date=${verificationDate()}`)

  const events = body.canonical!.events
  expect(eventByName(events, 'Synthetic Snapshot Food').meal_type).toBe('breakfast')
  expect(eventByName(events, 'Synthetic Metadata Food').meal_type).toBe('lunch')
  expect(eventByName(events, 'Unbenannter Eintrag').meal_type).toBeNull()
  expect(eventByName(events, 'Synthetic Snapshot Food').serving_unit).toBeNull()
  expect(eventByName(events, 'Synthetic Metadata Food').serving_unit).toBe('g')
  expect(eventByName(events, 'Synthetic Snapshot Food').local_time).toBeNull()
  expect(eventByName(events, 'Synthetic Metadata Food').local_time).toBe('08:15')
  expect(eventByName(events, 'Synthetic Unknown Meal Food').meal_type).toBeNull()

  await expect(page.getByRole('heading', { name: 'Ernährung' })).toBeVisible()
  await expect(page.getByText('Synthetic Snapshot Food', { exact: true })).toBeVisible()
  await expect(page.getByText('Synthetic Metadata Food', { exact: true })).toBeVisible()
  await expect(page.getByText('Unbenannter Eintrag', { exact: true })).toBeVisible()
  await expect(page.getByText('08:15', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Alle Quellen anzeigen' })).toBeVisible()

  const allResponsePromise = page.waitForResponse((response) => response.url().includes('/analytics/verification/nutrition') && response.url().includes('view=all') && response.status() === 200)
  await page.getByRole('button', { name: 'Alle Quellen anzeigen' }).click()
  const allResponse = await allResponsePromise
  const allBody = await allResponse.json() as NutritionResponse
  expect(allBody.providers.find((item) => item.provider_key === 'google_health')?.record_count).toBe(6)
  expect(allBody.providers.find((item) => item.provider_key === 'yazio')?.record_count).toBe(0)
  expect(allBody.providers.find((item) => item.provider_key === 'apple_health')?.record_count).toBe(0)

  const rendered = await page.locator('body').innerText()
  expect(rendered).not.toContain('Other User Food')
  expect(rendered).not.toContain('synthetic-google-client-secret')
  expect(rendered).not.toContain('synthetic-yazio-password')
  expect(rendered).not.toContain('source_instance_id')
  expect(rendered).not.toContain('raw payload')
})

test('keeps verification data scoped to the authenticated synthetic user', async ({ page }) => {
  await login(page, otherUsername)
  const responsePromise = page.waitForResponse((response) => response.url().includes('/analytics/verification/nutrition') && response.status() === 200)
  await page.goto('/ernaehrung')
  const response = await responsePromise
  const body = await response.json() as NutritionResponse
  expect(body.canonical?.record_count).toBe(6)
  expect(eventByName(body.canonical!.events, 'Other User Food').display_name).toBe('Other User Food')
  expect(body.canonical!.events.some((event) => event.display_name === 'Synthetic Snapshot Food')).toBe(false)
  await expect(page.getByText('Other User Food', { exact: true })).toBeVisible()
  await expect(page.getByText('Synthetic Snapshot Food', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Abmelden' }).click()
  await expect(page).toHaveURL(/\/login$/)
})

test('keeps Google activity as one daily rollup and exposes YAZIO separately', async ({ page }) => {
  await login(page)
  const responsePromise = page.waitForResponse((response) => response.url().includes('/analytics/verification/activity') && response.status() === 200)
  await page.goto('/aktivitaet')
  const response = await responsePromise
  const body = await response.json() as ActivityResponse
  const availableGoogleDays = body.days.filter((day) => day.canonical?.status === 'available')
  expect(availableGoogleDays).toHaveLength(2)
  expect(availableGoogleDays.every((day) => day.canonical?.provider_key === 'google_health')).toBe(true)
  expect(availableGoogleDays.map((day) => day.canonical?.record_count)).toEqual([1, 1])
  expect(availableGoogleDays.map((day) => day.canonical?.active_energy_kcal)).toEqual([380, 420])

  await expect(page.getByRole('heading', { name: 'Aktivität' })).toBeVisible()
  await expect(page.getByText('Google Health', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Alle Quellen anzeigen' })).toBeVisible()

  const allResponsePromise = page.waitForResponse((response) => response.url().includes('/analytics/verification/activity') && response.url().includes('view=all') && response.status() === 200)
  await page.getByRole('button', { name: 'Alle Quellen anzeigen' }).click()
  const allResponse = await allResponsePromise
  const allBody = await allResponse.json() as ActivityResponse
  const today = allBody.days.find((day) => day.date === verificationDate()) ?? allBody.days.at(-1)!
  const google = today.providers.find((record) => record.provider_key === 'google_health')!
  const yazio = today.providers.find((record) => record.provider_key === 'yazio')!
  expect(google.record_count).toBe(1)
  expect(google.active_energy_kcal).toBe(420)
  expect(yazio.record_count).toBe(2)
  expect(yazio.active_energy_kcal).toBe(333)
  expect(google.active_energy_kcal).not.toBe(753)
})

test('shows Google weight and persisted provider priorities', async ({ page }) => {
  await login(page)
  const weightResponsePromise = page.waitForResponse((response) => response.url().includes('/analytics/weight') && response.status() === 200)
  await page.goto('/gewicht')
  const weightResponse = await weightResponsePromise
  const weightBody = await weightResponse.json() as { selected_provider: { provider_key: string } | null; points: Array<{ weight_kg: number }> }
  expect(weightBody.selected_provider?.provider_key).toBe('google_health')
  expect(weightBody.points.at(-1)?.weight_kg).toBe(72.4)
  await expect(page.getByRole('heading', { name: 'Gewicht' })).toBeVisible()
  await expect(page.getByText('Google Health', { exact: true }).first()).toBeVisible()

  await page.goto('/konto/datenquellen')
  await expect(page.getByRole('heading', { name: 'Priorität der Datenquellen' })).toBeVisible()
  for (const area of ['nutrition', 'activity_energy', 'weight']) {
    const rows = page.locator(`[data-area="${area}"] .provider-priority-row`)
    await expect(rows).toHaveCount(3)
    await expect(rows.nth(0)).toHaveAttribute('data-provider-key', 'google_health')
    await expect(rows.nth(1)).toHaveAttribute('data-provider-key', 'yazio')
    await expect(rows.nth(2)).toHaveAttribute('data-provider-key', 'apple_health')
  }
})
