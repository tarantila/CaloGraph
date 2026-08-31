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
    activity_mode: 'full',
    activity_source_type: 'apple_health_xml',
    active_energy_kcal: 520,
    activity_credit_kcal: 520,
    activity_data_status: 'credited',
    effective_budget_kcal: 2720,
    effective_maintenance_kcal: null,
    effective_deviation_kcal: calories - 2720,
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
            activity_credit_kcal: 3640,
            effective_budget_kcal: 19040,
            effective_deviation_kcal: -10988,
            effective_remaining_kcal: 10988,
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
            target_weight_min_kg: null,
            target_weight_max_kg: null,
            activity_mode: 'full',
            activity_source_type: 'apple_health_xml',
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
    if (path.endsWith('/analytics/trends')) {
      return route.fulfill({
        json: {
          points,
          budget_balance: {
            tracked_days: 30,
            within_budget_days: 23,
            over_budget_days: 5,
            over_maintenance_days: 2,
            unclassified_budget_days: 0,
          },
        },
      })
    }
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'review-csrf' } })
    }
    if (path.endsWith('/achievements/reconcile')) {
      return route.fulfill({ json: { achievements: [], newly_unlocked: [] } })
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.setViewportSize({ width: 1440, height: 1024 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Ernährungsüberblick' })).toBeVisible()
  await expect(page.getByText('Ernährungs-Analytics')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Alle' })).toBeVisible()
  await expect(page.getByText('Selbst gehostet')).toHaveCount(0)
  await expect(page.getByText('Verbleibend nach Aktivität').first()).toBeVisible()
  await expect(page.getByText('inkl. +520 kcal durch Aktivitäten')).toBeVisible()
  await expect(page.getByText(/Basisbudget 15.400 kcal/)).toBeVisible()
  await expect(page.getByText(/Aktivitätsgutschrift \+3.640 kcal/)).toBeVisible()
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
            element.textContent?.includes('Effektives Budget')
          )
        })
        return tooltip?.textContent ?? ''
      }),
    )
    .toContain('Effektives Budget')
  await expect
    .poll(async () =>
      page.evaluate(() =>
        [...document.querySelectorAll<HTMLDivElement>('div')].find((element) => {
          const style = getComputedStyle(element)
          return (
            style.position === 'absolute' &&
            element.offsetParent !== null &&
            element.textContent?.includes('Aktivitätsgutschrift')
          )
        })?.textContent ?? '',
      ),
    )
    .toContain('Aktivitätsgutschrift: +520 kcal')

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
  await page.goto('/trends')
  await expect(page.getByRole('heading', { name: 'Trends' })).toBeVisible()
  const budgetBalance = page.locator('.budget-balance-section')
  await expect(budgetBalance).toBeVisible()
  await expect(budgetBalance).toContainText('Budgetbilanz')
  await expect(budgetBalance).toContainText('Getrackte Tage')
  await expect(budgetBalance).toContainText('30')
  await expect(budgetBalance).toContainText('Im Budget')
  await expect(budgetBalance).toContainText('23')
  const trendsChart = page.getByRole('img', { name: 'Kalorien und gleitende Mittelwerte' })
  await expect(trendsChart).toBeVisible()
  const trendsBox = await trendsChart.boundingBox()
  expect(trendsBox).not.toBeNull()
  await page.mouse.move(
    trendsBox!.x + trendsBox!.width * 0.72,
    trendsBox!.y + trendsBox!.height * 0.38,
  )
  await expect
    .poll(async () =>
      page.evaluate(() =>
        [...document.querySelectorAll<HTMLDivElement>('div')].find((element) => {
          const style = getComputedStyle(element)
          return (
            style.position === 'absolute' &&
            element.offsetParent !== null &&
            element.textContent?.includes('Effektives Tagesbudget')
          )
        })?.textContent ?? '',
      ),
    )
    .toContain('Effektives Tagesbudget')
  await expect
    .poll(async () =>
      page.evaluate(() =>
        [...document.querySelectorAll<HTMLDivElement>('div')].find((element) => {
          const style = getComputedStyle(element)
          return (
            style.position === 'absolute' &&
            element.offsetParent !== null &&
            element.textContent?.includes('Aktivitätsgutschrift')
          )
        })?.textContent ?? '',
      ),
    )
    .toContain('Aktivitätsgutschrift')
  await page.screenshot({ path: 'test-results/trends-activity-credit.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(trendsChart).toBeVisible()
  await page.screenshot({ path: 'test-results/trends-activity-credit-mobile.png', fullPage: true })

  expect(browserErrors).toEqual([])
})
