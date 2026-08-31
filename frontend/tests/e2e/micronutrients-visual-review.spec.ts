import { expect, test } from '@playwright/test'

const nutrients = [
  {
    id: 'vitamin.d',
    metric_type: 'vitamin_d_ug',
    label: 'Vitamin D',
    category: 'vitamin',
    unit: 'ug',
    eu_nrv: 5,
    total: 48,
    average_daily: 1.6,
    days_with_value: 24,
    coverage_ratio: 0.8,
    percent_of_nrv: 32,
    status: 'below_orientation',
  },
  {
    id: 'vitamin.c',
    metric_type: 'vitamin_c_mg',
    label: 'Vitamin C',
    category: 'vitamin',
    unit: 'mg',
    eu_nrv: 80,
    total: 2760,
    average_daily: 92,
    days_with_value: 30,
    coverage_ratio: 1,
    percent_of_nrv: 115,
    status: 'covered',
  },
  {
    id: 'mineral.iron',
    metric_type: 'iron_mg',
    label: 'Eisen',
    category: 'mineral',
    unit: 'mg',
    eu_nrv: 14,
    total: 294,
    average_daily: 9.8,
    days_with_value: 30,
    coverage_ratio: 1,
    percent_of_nrv: 70,
    status: 'below_orientation',
  },
  {
    id: 'mineral.choline',
    metric_type: 'choline_mg',
    label: 'Cholin',
    category: 'mineral',
    unit: 'mg',
    eu_nrv: null,
    total: 4800,
    average_daily: 160,
    days_with_value: 12,
    coverage_ratio: 0.4,
    percent_of_nrv: null,
    status: 'insufficient_data',
  },
]

test('micronutrient analysis shows source coverage and neutral orientation', async ({ page }) => {
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
          id: 'micro-review',
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
          id: 'micro-target',
          valid_from: '2026-01-01',
          valid_to: null,
          calories_kcal: 2000,
          maintenance_kcal: 2300,
          protein_g: 140,
          carbs_g: null,
          fat_g: null,
          fiber_g: null,
          target_weight_min_kg: null,
          target_weight_max_kg: null,
        }],
      })
    }
    if (path.endsWith('/analytics/micronutrients')) {
      return route.fulfill({
        json: {
          start_date: '2026-06-24',
          end_date: '2026-07-23',
          source: 'yazio_export_v1',
          recorded_days: 30,
          last_updated_at: '2026-07-23T12:30:00Z',
          available_sources: [
            {
              source_type: 'yazio_export_v1',
              last_updated_at: '2026-07-23T12:30:00Z',
            },
          ],
          definition: {
            coverage_threshold: 0.7,
            orientation_threshold_percent: 80,
          },
          nutrients,
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

  await page.setViewportSize({ width: 1440, height: 1100 })
  await page.goto('/mikronaehrstoffe')

  await expect(page.getByRole('heading', { name: 'Mikronährstoffanalyse' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Vitamine' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Mineralstoffe' })).toBeVisible()
  await expect(page.getByText('Vitamin D', { exact: true })).toBeVisible()
  await expect(page.getByText('Eisen', { exact: true })).toBeVisible()
  await expect(page.getByText('Kein EU-Referenzwert festgelegt')).toBeVisible()
  await expect(page.getByText('Unter Orientierung').first()).toBeVisible()
  await expect(page.getByText('Noch zu wenige Angaben', { exact: true })).toBeVisible()
  await expect(page.getByText('Anteil am EU-Referenzwert').first()).toBeVisible()
  await expect(page.getByText(/mindestens 21 nötig/).first()).toBeVisible()
  await expect(page.getByText(/keine Diagnose/i)).toBeVisible()
  await expect(page.getByRole('button', { name: '60 Tage aus YAZIO nachladen' })).toBeVisible()
  await expect(page.locator('.nutrient-progress')).toHaveCount(3)

  await page.screenshot({
    path: 'test-results/micronutrients-analysis.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})
