import { expect, test } from '@playwright/test'

const calorieValues = [
  1760, 1890, 2010, 1920, 2140, 1650, 2230,
  1980, 2050, 2310, 1870, 2190, 2440, 1730,
  1960, 2080, 2250, 1810, 1990, 2370, 2160,
  1900, 2020, 2290, 1840, 2410, 1970,
]
const budget = 2000
const maintenance = 2300
const calendarDays = calorieValues.map((calories, index) => ({
  date: `2026-07-${`${index + 1}`.padStart(2, '0')}`,
  calories_kcal: `${calories}.000`,
  target_kcal: `${budget}.000`,
  maintenance_kcal: `${maintenance}.000`,
  deviation_kcal: `${calories - budget}.000`,
  activity_mode: 'off',
  activity_source_type: null,
  active_energy_kcal: null,
  activity_credit_kcal: 0,
  activity_data_status: 'disabled',
  effective_budget_kcal: `${budget}.000`,
  effective_maintenance_kcal: `${maintenance}.000`,
  effective_deviation_kcal: `${calories - budget}.000`,
  protein_g: '140.000',
  carbs_g: '210.000',
  fat_g: '70.000',
  tracking_status: 'complete',
  tracking_score: 1,
  tracking_reasons: ['Kalorienwert vorhanden'],
  classification:
    calories <= budget
      ? 'under_budget'
      : calories <= maintenance
        ? 'over_budget'
        : 'above_maintenance',
}))

test('calendar explains budget and maintenance thresholds by month', async ({ page }) => {
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
          id: 'calendar-review',
          username: 'design-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
        },
      })
    }
    if (path.endsWith('/settings/targets')) {
      return route.fulfill({
        json: [{
          id: 'calendar-target',
          valid_from: '2026-01-01',
          valid_to: null,
          calories_kcal: 2000,
          maintenance_kcal: 2300,
          protein_g: 140,
          carbs_g: null,
          fat_g: null,
          fiber_g: null,
          activity_mode: 'off',
          activity_source_type: null,
        }],
      })
    }
    if (path.endsWith('/analytics/calendar')) {
      return route.fulfill({ json: { days: calendarDays } })
    }
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'review-csrf' } })
    }
    if (path.endsWith('/achievements/reconcile')) {
      return route.fulfill({ json: { achievements: [], newly_unlocked: [] } })
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.setViewportSize({ width: 1440, height: 1150 })
  await page.goto('/kalender')

  await expect(page.getByRole('heading', { name: 'Kalender' })).toBeVisible()
  await expect(page.getByText('Erfasste Tage')).toBeVisible()
  await expect(page.getByText('Über Budget', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('Über Budget und Erhaltungsbedarf', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('NaN')).toHaveCount(0)
  await expect(page.locator('.calendar-day.under_budget')).not.toHaveCount(0)
  await expect(page.locator('.calendar-day.over_budget')).not.toHaveCount(0)
  await expect(page.locator('.calendar-day.above_maintenance')).not.toHaveCount(0)
  await expect(page.locator('progress.calendar-calorie-progress')).toHaveCount(
    calendarDays.length,
  )
  await expect(
    page.getByRole('progressbar', { name: '1.760 von 2.000 kcal Tagesbudget' }),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Nächster Monat' })).toBeDisabled()

  await page.screenshot({
    path: 'test-results/calendar-analysis.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})
