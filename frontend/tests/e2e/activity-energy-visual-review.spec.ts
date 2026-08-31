import { expect, test } from '@playwright/test'

test('target settings expose a source-specific activity credit', async ({ page }) => {
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
          id: 'activity-review',
          username: 'design-review',
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
    if (path.endsWith('/settings/onboarding')) {
      return route.fulfill({ json: { mode: 'legacy', required: false, completed: true, current_step: 'completed' } })
    }
    if (path.endsWith('/settings/profile')) {
      return route.fulfill({
        json: {
          id: 'activity-review',
          username: 'design-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          preferred_weight_unit: 'kg',
          raw_payload_retention_days: 0,
          is_admin: false,
          is_active: true,
          deactivated_at: null,
        },
      })
    }
    if (path.endsWith('/settings/targets')) {
      return route.fulfill({
        json: [
          {
            id: 'activity-target',
            valid_from: '2026-08-01',
            valid_to: null,
            calories_kcal: 2000,
            maintenance_kcal: 2400,
            protein_g: 140,
            carbs_g: 220,
            fat_g: 70,
            fiber_g: 30,
            target_weight_min_kg: null,
            target_weight_max_kg: null,
            activity_mode: 'full',
            activity_source_type: 'apple_health_xml',
          },
        ],
      })
    }
    if (path.endsWith('/settings/activity-sources')) {
      return route.fulfill({
        json: [
          { source_type: 'apple_health_xml' },
          { source_type: 'yazio_export_v1' },
        ],
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

  await page.setViewportSize({ width: 1440, height: 1080 })
  await page.goto('/konto/budgets-und-ziele')

  await expect(page.getByRole('heading', { name: 'Budgets & Ziele' })).toBeVisible()
  const targetWeightCard = page.locator('.target-weight-settings')
  await expect(targetWeightCard).toBeVisible()
  await expect(targetWeightCard.evaluate((element) => element.tagName)).resolves.toBe('FIELDSET')
  await expect(targetWeightCard.locator('legend')).toHaveText('Zielgewicht')
  await expect(page.getByRole('radiogroup', { name: 'Zielgewicht festlegen' })).toBeVisible()
  await expect(targetWeightCard).toContainText('Kein Zielgewicht')
  await expect(targetWeightCard).toContainText('Festes Zielgewicht')
  await expect(targetWeightCard).toContainText('Zielbereich')
  await page.getByRole('radio', { name: 'Zielbereich' }).check()
  const desktopRangeColumns = await page.locator('.target-weight-range').evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(desktopRangeColumns).toBe(2)
  await expect(page.locator('.target-weight-range input')).toHaveCount(2)
  await expect(page.locator('legend', { hasText: 'Aktivitätskalorien' })).toBeVisible()
  const activityCard = page.locator('.activity-target-settings')
  const activitySwitch = page.getByRole('switch', { name: 'Aktivitätskalorien berücksichtigen' })
  await expect(activityCard.locator('.activity-status-badge')).toHaveText('Aktiv')
  await expect(activitySwitch).toBeChecked()
  await expect(page.locator('select[name="activity-source"]')).toHaveValue('apple_health_xml')
  await expect(page.locator('select[name="activity-source"]')).toContainText('Apple Health')
  await expect(page.getByText('An · Apple Health')).toBeVisible()
  await expect(activityCard).toContainText('Protein- und Makroziele bleiben unverändert.')

  await page.screenshot({ path: 'test-results/activity-energy-target-settings.png', fullPage: true })
  await activitySwitch.uncheck()
  await expect(activityCard.locator('.activity-status-badge')).toHaveText('Deaktiviert')
  await expect(page.locator('select[name="activity-source"]')).toBeHidden()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(activityCard).toBeVisible()
  const mobileRangeColumns = await page.locator('.target-weight-range').evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  const mobileModeColumns = await page.locator('.target-weight-mode-options').evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(mobileRangeColumns).toBe(1)
  expect(mobileModeColumns).toBe(1)
  await page.screenshot({ path: 'test-results/activity-energy-target-settings-mobile.png', fullPage: true })
  expect(browserErrors).toEqual([])
})
