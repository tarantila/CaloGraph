import { expect, test } from '@playwright/test'

const calorieValues = [
  1820, 1960, 1740, 2050, 2140, 1890, 2210, 2010, 2320, 2080,
  1870, 1980, 2276, 1606, 2021, 2314, 1875, 1842, 1940, 2180,
  2030, 1760, 2250, 2120, 1890, 2370, 2060, 1930, 2140, 1842,
]

const points = calorieValues.map((calories, index) => {
  const day = new Date(Date.UTC(2026, 5, 24 + index))
  const available = calorieValues.slice(Math.max(0, index - 6), index + 1)
  return {
    date: day.toISOString().slice(0, 10),
    calories_kcal: calories,
    protein_g: 108 + (index % 6) * 5,
    carbs_g: 178 + (index % 7) * 9,
    fat_g: 56 + (index % 5) * 4,
    target_kcal: 2200,
    deviation_kcal: calories - 2200,
    active_energy_kcal: 520,
    steps: 9400,
    tracking_status: 'complete',
    tracking_score: 1,
    tracking_reasons: [],
    average_7d: available.reduce((total, value) => total + value, 0) / available.length,
  }
})

test('dashboard periods, calorie budget, weekly icons and macro tooltip order', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/me')) {
      return route.fulfill({
        json: {
          id: 'visual-review',
          username: 'design-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
        },
      })
    }
    if (path.endsWith('/dashboard/summary')) {
      return route.fulfill({
        json: {
          today: points.at(-1),
          week: {
            consumed_kcal: 8052,
            budget_kcal: 15400,
            deviation_kcal: -7348,
            remaining_kcal: 7348,
          },
          protein_7d_average_g: 122,
          last_import_at: '2026-07-23T08:42:00+02:00',
          data_start_date: '2026-05-20',
          data_end_date: '2026-07-23',
          data_day_count: 65,
        },
      })
    }
    if (path.endsWith('/settings/targets')) {
      return route.fulfill({
        json: [
          {
            id: 'target',
            valid_from: '2026-01-01',
            valid_to: null,
            calories_kcal: 2200,
            protein_g: 140,
            carbs_g: 230,
            fat_g: 70,
            fiber_g: 30,
            water_ml: 2500,
          },
        ],
      })
    }
    if (path.endsWith('/imports')) {
      return route.fulfill({
        json: [
          {
            id: 'import',
            source_type: 'yazio_api',
            client_identifier: null,
            status: 'completed',
            started_at: '2026-07-23T08:41:00+02:00',
            finished_at: '2026-07-23T08:42:00+02:00',
            received: 7,
            inserted: 7,
            updated: 0,
            skipped: 0,
            failed: 0,
            unknown_types: [],
            error_message: null,
          },
        ],
      })
    }
    if (path.endsWith('/yazio/status')) {
      return route.fulfill({
        json: {
          available: true,
          configured: true,
          sync_enabled: true,
          sync_interval_minutes: 360,
          sync_days: 7,
          last_attempt_at: '2026-07-23T08:42:00+02:00',
          last_success_at: '2026-07-23T08:42:00+02:00',
          next_sync_at: '2026-07-23T14:55:00+02:00',
          last_error: null,
        },
      })
    }
    if (path.endsWith('/analytics/trends')) return route.fulfill({ json: { points } })
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.setViewportSize({ width: 1440, height: 1024 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await expect(page.getByText('Ernährungs-Analytics')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Alle' })).toBeVisible()
  await expect(page.getByText('Selbst gehostet')).toHaveCount(0)
  await expect(page.getByText('Wochenrest')).toBeVisible()
  await expect(page.getByText(/Mo–So · 8.052 von 15.400 kcal/)).toBeVisible()
  await expect(page.locator('.dashboard-heading .sync-status')).toHaveCount(0)

  const dataStatusCard = page.locator('.quality-card')
  await expect(dataStatusCard.getByRole('heading', { name: 'Datenstatus' })).toBeVisible()
  await expect(dataStatusCard.getByText('Datenabdeckung')).toBeVisible()
  await expect(dataStatusCard.getByText(/30 von 30 Tagen/)).toBeVisible()
  await expect(dataStatusCard.getByText('Keine Datenlücken')).toBeVisible()
  await expect(dataStatusCard.getByText('YAZIO')).toBeVisible()
  await expect(dataStatusCard.getByText(/Zuletzt synchronisiert:/)).toBeVisible()

  const weeklyCard = page.locator('.weekly-summary-card')
  await expect(weeklyCard.locator('.weekly-summary-icon')).toHaveCount(4)
  await expect(weeklyCard.locator('svg')).toHaveCount(5)

  const calorieChart = page.getByRole('img', { name: 'Kalorienaufnahme' })
  const calorieBox = await calorieChart.boundingBox()
  expect(calorieBox).not.toBeNull()
  await page.mouse.move(
    calorieBox!.x + calorieBox!.width * 0.72,
    calorieBox!.y + calorieBox!.height * 0.38,
  )
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const tooltip = [...document.querySelectorAll<HTMLDivElement>('div')].find((element) => {
          const style = getComputedStyle(element)
          return (
            style.position === 'absolute' &&
            element.offsetParent !== null &&
            element.textContent?.includes('Tagesbudget')
          )
        })
        return tooltip?.textContent ?? ''
      }),
    )
    .toContain('Tagesbudget')

  const macroChart = page.getByRole('img', { name: 'Makronährstoff-Verteilung' })
  const box = await macroChart.boundingBox()
  expect(box).not.toBeNull()
  await page.mouse.move(box!.x + box!.width * 0.72, box!.y + box!.height * 0.38)

  await expect
    .poll(async () =>
      page.evaluate(() => {
        const tooltip = [...document.querySelectorAll<HTMLDivElement>('div')].find((element) => {
          const style = getComputedStyle(element)
          return (
            style.position === 'absolute' &&
            element.offsetParent !== null &&
            element.textContent?.includes('Fett') &&
            element.textContent.includes('Kohlenhydrate') &&
            element.textContent.includes('Protein')
          )
        })
        return tooltip?.textContent ?? ''
      }),
    )
    .toMatch(/Fett.*Kohlenhydrate.*Protein/s)

  await weeklyCard.screenshot({ path: 'test-results/weekly-summary-icons.png' })
  await dataStatusCard.screenshot({ path: 'test-results/data-status.png' })
  await page.screenshot({ path: 'test-results/overview-icons-and-tooltip.png', fullPage: true })
  await page.screenshot({ path: 'test-results/overview-periods-and-budget.png', fullPage: true })
  expect(browserErrors).toEqual([])
})
