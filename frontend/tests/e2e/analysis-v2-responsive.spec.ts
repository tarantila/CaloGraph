import { expect, test, type Page } from '@playwright/test'

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

async function installSyntheticAnalysisApi(page: Page): Promise<void> {
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
}

test('Analysis cards stay readable across viewports and detail cards have useful insets', async ({ page }) => {
  await installSyntheticAnalysisApi(page)

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
  expect(await layout()).toMatchObject({ columns: 4 })
  await page.goto('/analyse/koerper/gewicht')
  const desktopPicker = page.locator('.analysis-period-picker')
  await desktopPicker.locator('.analysis-period-custom-button').click()
  const desktopPickerColumns = await desktopPicker.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(desktopPickerColumns).toBe(2)
  await expect(desktopPicker.locator('.analysis-period-option')).toHaveCount(5)
  await expect(desktopPicker.locator('.analysis-period-custom-form')).toBeVisible()
  await expect(desktopPicker.locator('.analysis-period-strip').getByRole('button', { name: 'Alle Daten' })).toBeVisible()
  await page.goto('/analyse')


  await page.setViewportSize({ width: 390, height: 844 })
  expect(await layout()).toMatchObject({ columns: 1 })
  expect((await layout()).copyWidths.every((width) => width >= 100)).toBe(true)
  await page.goto('/analyse/koerper/gewicht')
  const mobileWeightPicker = page.locator('.analysis-period-picker')
  await mobileWeightPicker.locator('.analysis-period-trigger').click()
  await expect(mobileWeightPicker.locator('.analysis-period-option')).toHaveCount(5)
  const mobileWeightPickerColumns = await mobileWeightPicker.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(mobileWeightPickerColumns).toBe(1)
  await page.goto('/analyse')
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
  await expect(picker.locator('.analysis-period-custom-form')).toBeVisible()
  await expect(picker.locator('.analysis-period-custom-form input[type="text"]')).toHaveCount(2)
  const mobilePickerColumns = await picker.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(mobilePickerColumns).toBe(1)
  const nativeDatePickers = picker.locator('.analysis-period-custom-form .date-input-native-picker')
  await expect(nativeDatePickers).toHaveCount(2)
  for (const nativeDatePicker of await nativeDatePickers.all()) {
    await expect(nativeDatePicker).toHaveCSS('opacity', '0')
    await expect(nativeDatePicker).toHaveCSS('pointer-events', 'none')
  }
  await expectUsefulInsets('.micronutrient-summary > article')
  await expectUsefulInsets('.micronutrient-daily-card')

})

for (const width of [320, 375]) {
  test(`Analysis charts, overflow and period overlay fit ${width}px with visible keyboard focus`, async ({ page }) => {
    await installSyntheticAnalysisApi(page)
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/analyse')
    const handle = page.locator('[data-analysis-card="weight_kg"] .analysis-drag-handle')
    await expect(handle).toBeVisible()
    const handleBox = await handle.boundingBox()
    expect(handleBox!.width).toBeGreaterThanOrEqual(44)
    expect(handleBox!.height).toBeGreaterThanOrEqual(44)
    await handle.focus()
    await page.keyboard.press('Tab')
    const focused = page.locator(':focus')
    await expect(focused).toBeVisible()
    const focusPaint = await focused.evaluate((element) => {
      const style = getComputedStyle(element)
      return {
        keyboardFocus: element.matches(':focus-visible'),
        outlineVisible: style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) >= 2
          && style.outlineColor !== 'transparent' && style.outlineColor !== 'rgba(0, 0, 0, 0)',
        box: element.getBoundingClientRect().toJSON(),
      }
    })
    expect(focusPaint.keyboardFocus).toBe(true)
    expect(focusPaint.outlineVisible).toBe(true)
    expect(focusPaint.box.x).toBeGreaterThanOrEqual(2)
    expect(focusPaint.box.right).toBeLessThanOrEqual(width - 2)
    await page.locator('[data-analysis-card="weight_kg"] .analysis-overflow summary').focus()
    await page.keyboard.press('Enter')
    const overflow = page.locator('[data-analysis-card="weight_kg"] .analysis-overflow-menu')
    await expect(overflow).toBeVisible()
    const overflowBox = await overflow.boundingBox()
    expect(overflowBox!.x).toBeGreaterThanOrEqual(0)
    expect(overflowBox!.x + overflowBox!.width).toBeLessThanOrEqual(width)
    await page.keyboard.press('Tab')
    const moveAction = page.locator(':focus')
    await expect(moveAction).toBeVisible()
    expect(await moveAction.evaluate((element) => {
      const style = getComputedStyle(element)
      return element.matches(':focus-visible') && style.outlineStyle !== 'none'
        && Number.parseFloat(style.outlineWidth) >= 2
        && style.outlineColor !== 'rgba(0, 0, 0, 0)' && style.outlineColor !== 'transparent'
    })).toBe(true)

    for (const path of ['/analyse/koerper/gewicht', '/analyse/aktivitaet/aktive-energie', '/analyse/mikronaehrstoffe/vitamin_a_ug']) {
      await page.goto(`${path}?period=custom&start=2026-09-22&end=2026-09-28`)
      const chart = page.locator('.chart-card [role="img"]')
      await expect(chart.first()).toBeVisible()
      for (const surface of await chart.all()) {
        const box = await surface.boundingBox()
        expect(box!.x).toBeGreaterThanOrEqual(0)
        expect(box!.x + box!.width).toBeLessThanOrEqual(width)
        expect(await surface.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      const trigger = page.locator('.analysis-period-trigger')
      await trigger.focus()
      await page.keyboard.press('Enter')
      const popover = page.locator('.analysis-period-popover')
      await expect(popover).toBeVisible()
      const box = await popover.boundingBox()
      expect(box!.x).toBeGreaterThanOrEqual(0)
      expect(box!.x + box!.width).toBeLessThanOrEqual(width)
      expect(await popover.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
      await page.keyboard.press('Escape')
      await expect(trigger).toBeFocused()
      await expect(popover).not.toBeVisible()
    }
  })
}
