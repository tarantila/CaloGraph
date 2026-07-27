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
  const browserErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.route('**/api/v1/**', async (route) => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname
    if (path.endsWith('/auth/me')) {
      return route.fulfill({
        json: {
          id: 'regression-review',
          username: 'design-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
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
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/tage')

  await expect(page.getByRole('heading', { name: 'Tagesverlauf' })).toBeVisible()
  await expect(page.getByText('1.800 kcal', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('NaN')).toHaveCount(0)

  const navigation = (await page.locator('.sidebar nav a').allTextContents()).map((label) =>
    label.trim(),
  )
  expect(navigation.indexOf('Kalender')).toBe(navigation.indexOf('Wochentage') + 1)

  await page.getByRole('link', { name: 'Wochentage' }).click()
  await expect(page.getByRole('heading', { name: 'Wochentagsanalyse' })).toBeVisible()
  const rangeSelect = page.getByLabel('Zeitraum')
  await expect(rangeSelect).toContainText('Aktuelle Woche')
  await expect(rangeSelect).toContainText('Letzte Woche')
  await expect(rangeSelect).toContainText('Letzte 180 Tage')
  await rangeSelect.selectOption('last-week')
  await expect(page).toHaveURL(/start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}/)

  await page.screenshot({
    path: 'test-results/weekday-date-filter.png',
    fullPage: true,
  })
  expect(browserErrors).toEqual([])
})
