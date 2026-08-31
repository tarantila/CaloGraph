import { expect, test, type Page } from '@playwright/test'

const routes = ['/mikronaehrstoffe', '/trends', '/wochentage', '/tage']

async function mockAnalyticsApi(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    }
    if (path.endsWith('/auth/me')) {
      return route.fulfill({
        json: {
          id: 'responsive-review',
          username: 'responsive-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
          is_active: true,
          deactivated_at: null,
        },
      })
    }
    if (path.endsWith('/settings/targets')) {
      return route.fulfill({ json: [{ id: 'target', valid_from: '2026-01-01', valid_to: null, calories_kcal: 2000, maintenance_kcal: null, protein_g: 120, carbs_g: null, fat_g: null, fiber_g: null, target_weight_min_kg: null, target_weight_max_kg: null }] })
    }
    if (path.endsWith('/achievements/reconcile')) {
      return route.fulfill({ json: { achievements: [], newly_unlocked: [] } })
    }
    if (path.endsWith('/analytics/micronutrients')) {
      return route.fulfill({ json: { available_sources: [{ source_type: 'yazio_export_v1', last_updated_at: null }], nutrients: [], recorded_days: 0, last_updated_at: null } })
    }
    if (path.endsWith('/analytics/trends')) {
      return route.fulfill({ json: { points: [], budget_balance: null } })
    }
    if (path.endsWith('/analytics/weekdays')) {
      return route.fulfill({ json: { weekdays: [] } })
    }
    if (path.endsWith('/analytics/daily')) {
      return route.fulfill({ json: [] })
    }
    return route.continue()
  })
}

test('analytics period filter switches at the responsive layout boundary', async ({ page }) => {
  test.setTimeout(120_000)
  await mockAnalyticsApi(page)
  for (const width of [1600, 1440, 1366, 1280, 1200, 1152, 1100, 1050, 1024, 768, 600, 480, 390]) {
    await page.setViewportSize({ width, height: 900 })
    for (const path of routes) {
      await test.step(`${width}px ${path}`, async () => {
        await page.goto(path, { waitUntil: 'domcontentloaded' })
        await expect(page.locator('h1')).toBeVisible({ timeout: 10_000 })
        const filter = page.locator('.analytics-period-filter')
        await expect(filter).toBeVisible()
        if (width >= 1440) {
          await expect(filter.locator('.analytics-period-filter-desktop')).toBeVisible()
          await expect(filter.locator('.analytics-period-filter-compact')).toBeHidden()
        } else {
          await expect(filter.locator('.analytics-period-filter-desktop')).toBeHidden()
          await expect(filter.locator('.analytics-period-filter-compact')).toBeVisible()
        }
        const titleBox = await page.locator('.analytics-page-heading-content > div').first().boundingBox()
        const filterBox = await filter.boundingBox()
        const desktopFormBox = await filter.locator('.analytics-period-filter-desktop form').boundingBox()
        const alignedFilterBox = width >= 1440 ? desktopFormBox : filterBox
        const rasterBox = await page.locator('.insight-strip').first().boundingBox()
        expect(titleBox).not.toBeNull()
        expect(alignedFilterBox).not.toBeNull()
        expect(rasterBox).not.toBeNull()
        expect(Math.abs((alignedFilterBox?.x ?? 0) + (alignedFilterBox?.width ?? 0) - ((rasterBox?.x ?? 0) + (rasterBox?.width ?? 0)))).toBeLessThanOrEqual(0.5)
        if (width >= 1440) {
          expect(Math.abs((filterBox?.width ?? 0) - 820)).toBeLessThanOrEqual(1)
          expect((titleBox?.x ?? 0) + (titleBox?.width ?? 0)).toBeLessThanOrEqual(desktopFormBox?.x ?? 0)
          expect(await filter.locator('.analytics-period-filter-desktop form').evaluate((element) => element.getBoundingClientRect().height)).toBeLessThanOrEqual(63)
          expect(await page.locator('.analytics-page-heading h1').evaluate((element) => element.scrollWidth)).toBeLessThanOrEqual(
            await page.locator('.analytics-page-heading h1').evaluate((element) => element.clientWidth),
          )
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
          await page.evaluate(() => document.documentElement.clientWidth),
        )
      })
    }
  }
})
test('compact custom controls keep keyboard focus and active semantics', async ({ page }) => {
  await mockAnalyticsApi(page)
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto('/tage')

  const filter = page.locator('.analytics-period-filter')
  await expect(filter.locator('.analytics-period-custom-fields')).toBeHidden()
  await filter.getByRole('button', { name: 'Individuell', exact: true }).click()
  await expect(filter.locator('.analytics-period-custom-fields')).toBeVisible()
  await expect(filter.locator('.analytics-period-button[aria-pressed="true"]')).toHaveText('Individuell')

  const firstDate = filter.locator('.analytics-period-custom-fields input[type="text"]').first()
  await firstDate.focus()
  await expect(firstDate).toBeFocused()
  await expect(filter.locator('.analytics-period-custom-fields button.compact-apply')).toBeVisible()
})
