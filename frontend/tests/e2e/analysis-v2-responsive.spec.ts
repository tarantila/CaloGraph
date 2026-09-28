import { expect, test } from '@playwright/test'

const registry = [
  {
    analysis_key: 'weight_kg', category_key: 'body', domain_key: 'weight_kg', status: 'available',
    card_capable: true, detail_capable: true, reader_key: 'analytics.weight',
    period_capability: { min_days: 1, max_days: 3661, custom_range: true },
  },
  {
    analysis_key: 'active_energy_kcal', category_key: 'activity', domain_key: 'active_energy_kcal', status: 'available',
    card_capable: true, detail_capable: true, reader_key: 'analytics.verification.activity',
    period_capability: { min_days: 1, max_days: 3661, custom_range: true },
  },
  {
    analysis_key: 'vitamin_a_ug', category_key: 'micronutrients', domain_key: 'vitamin_a_ug', status: 'available',
    card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients',
    period_capability: { min_days: 1, max_days: 31, custom_range: true },
  },
]

test('Analysis cards stay readable across viewports and detail cards have useful insets', async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname.replace(/^\/api\/v1/u, '')
    const responses: Record<string, unknown> = {
      '/auth/me': {
        id: 'analysis-layout-user', username: 'analysis-layout', language: 'de', timezone: 'Europe/Berlin',
        week_starts_on: 1, preferred_weight_unit: 'kg', raw_payload_retention_days: 0,
        is_admin: false, is_active: true, deactivated_at: null,
      },
      '/settings/onboarding': { mode: 'legacy', required: false, completed: true, current_step: 'completed' },
      '/achievements/reconcile': { achievements: [], newly_unlocked: [] },
      '/settings/analysis-preferences': { analysis_keys: ['weight_kg', 'active_energy_kcal', 'vitamin_a_ug'] },
      '/analytics/registry': { analyses: registry },
      '/analytics/weight': {
        start_date: '2026-09-22', end_date: '2026-09-28', selected_provider: null,
        points: [{ date: '2026-09-22', weight_kg: 70 }, { date: '2026-09-28', weight_kg: 69.5 }],
        summary: {
          measurement_count: 2, latest: { date: '2026-09-28', weight_kg: 69.5 }, change_kg: -0.5,
          average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70,
        },
      },
      '/analytics/verification/activity': {
        start_date: '2026-09-22', end_date: '2026-09-28', view: 'canonical',
        days: [{ date: '2026-09-28', canonical: {
          provider_key: 'yazio', status: 'available', active_energy_kcal: 240, record_count: 1, source_types: [],
        }, providers: [] }],
      },
      '/analytics/nutrition/micronutrients': {
        start_date: '2026-09-22', end_date: '2026-09-28', selected_provider: null, period_days: 7,
        metrics: [{ metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 20, days_with_data: 2, contributors: [] }],
        series: [{ date: '2026-09-22', provider_key: 'yazio', value: 10 }, { date: '2026-09-28', provider_key: 'yazio', value: 10 }],
      },
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses[path] ?? {}) })
  })

  await page.setViewportSize({ width: 1024, height: 900 })
  await page.goto('/analyse')
  const cards = page.locator('.analysis-card-list')
  await expect(cards.locator('.analysis-card')).toHaveCount(3)

  const layout = async () => cards.evaluate((grid) => {
    const columns = getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length
    const copyWidths = Array.from(grid.querySelectorAll<HTMLElement>('.analysis-card-copy'))
      .map((copy) => Math.round(copy.getBoundingClientRect().width))
    return { columns, copyWidths }
  })

  expect(await layout()).toMatchObject({ columns: 2 })
  expect((await layout()).copyWidths.every((width) => width >= 100)).toBe(true)

  await page.setViewportSize({ width: 1440, height: 900 })
  expect(await layout()).toMatchObject({ columns: 3 })

  await page.setViewportSize({ width: 390, height: 844 })
  expect(await layout()).toMatchObject({ columns: 1 })
  expect((await layout()).copyWidths.every((width) => width >= 100)).toBe(true)
  const expectUsefulInsets = async (selector: string) => {
    const cards = page.locator(selector)
    await expect(cards.first()).toBeVisible()
    const insets = await cards.evaluateAll((elements) => elements.map((element) => {
      const style = getComputedStyle(element)
      return { top: Number.parseFloat(style.paddingTop), left: Number.parseFloat(style.paddingLeft) }
    }))
    expect(insets.every(({ top, left }) => top >= 12 && left >= 12)).toBe(true)
  }

  await page.goto('/analyse/katalog')
  await expect(page.locator('.analysis-catalog-entry').first()).toBeVisible()
  const longCatalogTitle = page.locator('.analysis-catalog-title').first()
  await longCatalogTitle.locator('a, span').first().evaluate((element) => {
    element.textContent = 'ExtremLangBezeichneterMikronaehrstoffmetrikwertOhneTrennstellen'.repeat(2)
  })
  const longTitleLayout = await longCatalogTitle.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
      height: element.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(style.lineHeight),
    }
  })
  expect(longTitleLayout.scrollWidth).toBeLessThanOrEqual(longTitleLayout.clientWidth)
  expect(longTitleLayout.height).toBeGreaterThan(longTitleLayout.lineHeight * 1.5)

  await page.goto('/analyse/koerper/gewicht')
  await expectUsefulInsets('.weight-summary-primary')
  await expectUsefulInsets('.weight-reading-list')

  await page.goto('/analyse/aktivitaet/aktive-energie')
  await expectUsefulInsets('.activity-summary')
  await expectUsefulInsets('.activity-reading-list')

  await page.goto('/analyse/mikronaehrstoffe/vitamin_a_ug')
  const periodPickerHeight = await page.locator('.analysis-period-picker')
    .evaluate((picker) => picker.getBoundingClientRect().height)
  expect(periodPickerHeight).toBeLessThan(200)
  const picker = page.locator('.analysis-period-picker')
  await picker.locator('.analysis-period-trigger').click()
  const popover = picker.locator('.analysis-period-popover')
  await expect(popover).toBeVisible()
  const popoverBox = await popover.boundingBox()
  expect(popoverBox).not.toBeNull()
  expect(popoverBox!.x).toBeGreaterThanOrEqual(0)
  expect(popoverBox!.x + popoverBox!.width).toBeLessThanOrEqual(390)
  await picker.getByRole('button', { name: 'Benutzerdefiniert' }).click()
  await expect(picker.locator('.analysis-period-custom-form')).toBeVisible()
  await expect(picker.locator('.analysis-period-custom-form input[type="text"]')).toHaveCount(2)
  const nativeDatePickers = picker.locator('.analysis-period-custom-form .date-input-native-picker')
  await expect(nativeDatePickers).toHaveCount(2)
  for (const nativeDatePicker of await nativeDatePickers.all()) {
    await expect(nativeDatePicker).toHaveCSS('opacity', '0')
    await expect(nativeDatePicker).toHaveCSS('pointer-events', 'none')
  }
  await expectUsefulInsets('.micronutrient-summary > article')
  await expectUsefulInsets('.micronutrient-daily-card')

})
