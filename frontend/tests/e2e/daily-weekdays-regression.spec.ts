import { expect, test } from '@playwright/test'

const weekdays = [
  'Montag',
  'Dienstag',
  'Mittwoch',
  'Donnerstag',
  'Freitag',
  'Samstag',
  'Sonntag',
].map((label, weekday) => ({
  weekday,
  label,
  count: 4,
  mean_kcal: 1800 + weekday * 45,
  median_kcal: 1780 + weekday * 45,
  p25_kcal: 1650 + weekday * 40,
  p75_kcal: 1950 + weekday * 50,
  mean_deviation_kcal: -200 + weekday * 45,
  mean_protein_g: 120 + weekday * 3,
}))

test('daily averages, weekday ranges, and sidebar order stay consistent', async ({ page }) => {
  let profileLanguage: 'de' | 'en' = 'de'
  const browserErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.route('**/api/v1/**', async (route) => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    }
    if (path.endsWith('/auth/me')) {
      return route.fulfill({
        json: {
          id: 'regression-review',
          username: 'design-review',
          language: profileLanguage,
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
          id: 'regression-target',
          valid_from: '2026-01-01',
          valid_to: null,
          calories_kcal: 2000,
          maintenance_kcal: 2300,
          protein_g: 140,
          carbs_g: null,
          fat_g: null,
          fiber_g: null,
        }],
      })
    }
    if (path.endsWith('/settings/profile')) {
      const body = route.request().method() === 'PUT'
        ? route.request().postDataJSON() as { language?: string } | null
        : null
      if (body?.language === 'de' || body?.language === 'en') profileLanguage = body.language
      return route.fulfill({
        json: {
          id: 'regression-review',
          username: 'design-review',
          language: profileLanguage,
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
        },
      })
    }
    if (path.endsWith('/settings/tokens') || path.endsWith('/settings/passkeys')) {
      return route.fulfill({ json: [] })
    }
    if (path.endsWith('/settings/mfa')) {
      return route.fulfill({
        json: { totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 },
      })
    }
    if (path.endsWith('/yazio/status')) {
      return route.fulfill({
        json: {
          available: true,
          configured: false,
          sync_enabled: false,
          sync_interval_minutes: null,
          sync_days: null,
          last_attempt_at: null,
          last_success_at: null,
          next_sync_at: null,
          last_error: null,
        },
      })
    }
    if (path.endsWith('/analytics/daily')) {
      return route.fulfill({
        json: [
          {
            date: '2026-07-25',
            calories_kcal: '1600.000',
            target_kcal: '2000.000',
            maintenance_kcal: '2300.000',
            deviation_kcal: '-400.000',
            protein_g: '120.000',
            carbs_g: '180.000',
            fat_g: '60.000',
            tracking_status: 'complete',
            tracking_score: 1,
            tracking_reasons: ['Kalorienwert vorhanden'],
          },
          {
            date: '2026-07-26',
            calories_kcal: '2000.000',
            target_kcal: '2000.000',
            maintenance_kcal: '2300.000',
            deviation_kcal: '0.000',
            protein_g: '140.000',
            carbs_g: '220.000',
            fat_g: '70.000',
            tracking_status: 'complete',
            tracking_score: 1,
            tracking_reasons: ['Kalorienwert vorhanden'],
          },
        ],
      })
    }
    if (path.endsWith('/analytics/weekdays')) {
      const start = requestUrl.searchParams.get('start')
      const end = requestUrl.searchParams.get('end')
      const selectedDays =
        start && end
          ? Math.round(
              (new Date(`${end}T12:00:00Z`).getTime() -
                new Date(`${start}T12:00:00Z`).getTime()) /
                86_400_000,
            ) + 1
          : 180
      return route.fulfill({
        json: {
          weekdays: weekdays.map((item) => ({
            ...item,
            count: selectedDays <= 7 ? 1 : item.count,
          })),
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
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/tage')

  await expect(page.getByRole('heading', { name: 'Tagesverlauf' })).toBeVisible()
  await expect(page.getByText('1.800 kcal', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('NaN')).toHaveCount(0)

  const primaryNavigation = (await page.locator('.sidebar-primary-navigation a').allTextContents()).map((label) =>
    label.trim(),
  )
  expect(primaryNavigation).toEqual([
    'Übersicht',
    'Tagesverlauf',
    'Wochenbudget',
    'Wochentage',
    'Kalender',
    'Trends',
    'Mikronährstoffe',
    'Erfolge',
  ])
  const utilityNavigation = (await page.locator('.sidebar-utility-navigation a').allTextContents()).map((label) =>
    label.trim(),
  )
  expect(utilityNavigation).toEqual(['Konto'])
  expect(new Set([...primaryNavigation, ...utilityNavigation]).size).toBe(9)
  expect(page.locator('.sidebar nav a.active')).toHaveCount(1)
  await page.getByRole('link', { name: 'Wochentage' }).click()
  await expect(page.getByRole('heading', { name: 'Wochentagsanalyse' })).toBeVisible()
  const weekdayRows = page.locator('table tbody tr')
  await expect(weekdayRows).toHaveCount(7)
  await expect(weekdayRows.locator('td:first-child')).toHaveText(weekdays.map((item) => item.label))
  const rangeSelect = page.locator('.analytics-period-filter-desktop select')
  await expect(rangeSelect).toContainText('Aktuelle Woche')
  await expect(rangeSelect).toContainText('Letzte Woche')
  await expect(rangeSelect).toContainText('6 Monate')
  await rangeSelect.selectOption('last-week')
  await expect(page).toHaveURL(/start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}/)
  await page.goto('/konto/allgemeine-einstellungen')
  await expect(page.getByRole('heading', { name: 'Allgemeine Einstellungen' })).toBeVisible()
  await expect(page.locator('.sidebar-utility-navigation a.active')).toHaveAttribute('href', '/konto/persoenliche-daten')
  await page.locator('select[name="language"]').selectOption('en')
  await expect(page.locator('select[name="language"]')).toHaveValue('en')
  await page.getByRole('button', { name: 'Einstellungen speichern' }).click()
  await expect(page.getByRole('heading', { name: 'General settings' })).toBeVisible()
  await page.getByRole('link', { name: 'Weekdays' }).click()
  await expect(weekdayRows.locator('td:first-child')).toHaveText([
    'Monday',
    'Tuesday',
    'Wednesday',
    'Thursday',
    'Friday',
    'Saturday',
    'Sunday',
  ])

  await page.screenshot({
    path: 'test-results/weekday-date-filter.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})
