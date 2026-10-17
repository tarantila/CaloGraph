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
  ...['vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg', 'folate_ug', 'zinc_mg'].map((analysis_key) => ({
    analysis_key, category_key: 'micronutrients', domain_key: analysis_key, status: 'available',
    card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients',
    period_capability: { min_days: 1, max_days: 365, custom_range: true },
  })),
  ...['dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fiber_g', 'fat_g'].map((analysis_key) => ({
    analysis_key, category_key: 'nutrition', domain_key: analysis_key, status: 'available',
    card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.analysis',
    period_capability: { min_days: 1, max_days: 365, custom_range: true },
  })),
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
      '/settings/analysis-preferences': {
        active_card_ids: [
          'weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g',
          'carbohydrates_g', 'fiber_g', 'fat_g', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg',
          'folate_ug', 'zinc_mg',
        ],
      },
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
      '/analytics/nutrition/analysis': {
        start_date: '2026-09-22', end_date: '2026-09-28', selected_provider: { provider_key: 'yazio' }, period_days: 7,
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        days: [
          { date: '2026-09-22', provider_key: 'yazio', dietary_energy_kcal: 410, protein_g: 20, carbohydrates_g: 50, fat_g: 10, fiber_g: 4, sugar_g: 6, saturated_fat_g: 2, meals: [] },
          { date: '2026-09-24', provider_key: 'yazio', dietary_energy_kcal: 590, protein_g: 40, carbohydrates_g: 60, fat_g: 20, fiber_g: 6, sugar_g: 8, saturated_fat_g: 3, meals: [] },
        ],
      },
      '/analytics/daily': [],
      '/analytics/nutrition/micronutrients': {
        start_date: '2026-09-22', end_date: '2026-09-28', selected_provider: null, period_days: 7,
        metrics: [
          { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 20, days_with_data: 7, contributors: [] },
          { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: 600, days_with_data: 7, contributors: [] },
          { metric_key: 'vitamin_c_mg', label: 'Vitamin C', category: 'vitamin', unit: 'mg', eu_nrv: 80, total: 70, days_with_data: 6, contributors: [] },
          { metric_key: 'folate_ug', label: 'Folat', category: 'vitamin', unit: 'ug', eu_nrv: 200, total: 100, days_with_data: 5, contributors: [] },
          { metric_key: 'zinc_mg', label: 'Zink', category: 'mineral', unit: 'mg', eu_nrv: 10, total: 9, days_with_data: 4, contributors: [] },
        ],
        series: [],
      },
    }
    if (path === '/analytics/nutrition/micronutrients') {
      const response = responses[path] as {
        start_date: string; end_date: string; period_days: number;
        metrics: { metric_key: string; total: number; days_with_data: number }[];
        series: { date: string; provider_key: string; value: number }[];
      }
      const metricKey = requestUrl.searchParams.get('metric_key')
      if (metricKey) {
        response.metrics = response.metrics.filter((metric) => metric.metric_key === metricKey)
        const metric = response.metrics[0]!
        response.series = Array.from({ length: metric.days_with_data }, (_, day) => ({
          date: `2026-09-${String(22 + day).padStart(2, '0')}`,
          provider_key: 'yazio', value: metric.total / metric.days_with_data,
        }))
      }
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses[path] ?? {}) })
  })
}

test('Tages-Picker hält benutzerdefinierte Felder bei 820px im Viewport', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 820, height: 900 })
  await page.goto('/tage')

  const picker = page.locator('.analysis-period-picker')
  await expect(picker.locator('.analysis-period-custom-button')).toBeVisible()
  await picker.locator('.analysis-period-custom-button').click()
  const popover = picker.locator('.analysis-period-popover')
  await expect(popover).toBeVisible()
  await expect(popover.getByRole('button', { name: 'Zeitraum anwenden', exact: true })).toBeVisible()
  const bounds = await popover.evaluate((element) => {
    const rect = element.getBoundingClientRect()
    const controls = Array.from(element.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
      'input[type="text"], button[type="submit"]',
    )).map((control) => {
      const controlRect = control.getBoundingClientRect()
      return { left: controlRect.left, right: controlRect.right }
    })
    return { left: rect.left, right: rect.right, controls, viewportWidth: window.innerWidth }
  })
  expect(bounds.left).toBeGreaterThanOrEqual(0)
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewportWidth)
  expect(bounds.controls).toHaveLength(3)
  expect(bounds.controls.every(({ left, right }) => left >= 0 && right <= bounds.viewportWidth)).toBe(true)
})

test('Analysis-Detail-Picker bleibt an den Viewportkanten von 601–881px', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 601, height: 900 })
  await page.goto('/analyse/koerper/gewicht?period=30')

  const picker = page.locator('.analysis-period-picker')
  const customButton = picker.locator('.analysis-period-custom-button')
  const popover = picker.locator('.analysis-period-popover')
  await expect(customButton).toBeVisible()
  for (const width of [601, 800, 801, 820, 880, 881]) {
    await page.setViewportSize({ width, height: 900 })
    await customButton.click()
    await expect(popover).toBeVisible()
    const bounds = await popover.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      const controls = Array.from(element.querySelectorAll<HTMLInputElement | HTMLButtonElement>(
        'input[type="text"], button[type="submit"]',
      )).map((control) => {
        const controlRect = control.getBoundingClientRect()
        return { left: controlRect.left, right: controlRect.right }
      })
      return {
        left: rect.left,
        right: rect.right,
        controls,
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }
    })
    expect(bounds.left).toBeGreaterThanOrEqual(0)
    expect(bounds.right).toBeLessThanOrEqual(bounds.clientWidth)
    expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth)
    expect(bounds.controls).toHaveLength(3)
    expect(bounds.controls.every(({ left, right }) => left >= 0 && right <= bounds.clientWidth)).toBe(true)
    await page.keyboard.press('Escape')
    await expect(popover).toBeHidden()
  }
})

test('Analysis cards stay readable across viewports and detail cards have useful insets', async ({ page }) => {
  await installSyntheticAnalysisApi(page)

  await page.setViewportSize({ width: 1024, height: 900 })
  await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')
  const cards = page.locator('.analysis-card-list')
  const activeCards = cards.locator(':scope > li[data-card-key]')
  const addCard = cards.locator(':scope > li[data-analysis-add-card]')
  await expect(cards).toHaveCount(1)
  await expect(activeCards).toHaveCount(14)
  await expect(addCard).toHaveCount(1)

  const layout = async () => cards.evaluate((grid) => ({
    columns: getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length,
    cardWidths: Array.from(grid.querySelectorAll<HTMLElement>(':scope > li[data-card-key]'))
      .filter((card) => card.getClientRects().length > 0)
      .map((card) => Math.round(card.getBoundingClientRect().width)),
    cardKeys: Array.from(grid.querySelectorAll<HTMLElement>(':scope > li[data-card-key]'))
      .map((card) => card.dataset.cardKey),
    addTileLast: grid.lastElementChild?.matches('[data-analysis-add-card]') ?? false,
  }))

  const expectedOrder = [
    'weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g',
    'carbohydrates_g', 'fiber_g', 'fat_g', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg',
    'folate_ug', 'zinc_mg',
  ]
  expect((await layout()).cardKeys).toEqual(expectedOrder)
  expect(await layout()).toMatchObject({ columns: 2 })
  for (const width of [1201, 1440, 1568, 1680]) {
    await page.setViewportSize({ width, height: 900 })
    const currentLayout = await layout()
    expect(currentLayout.columns).toBe(4)
    expect(Math.min(...currentLayout.cardWidths)).toBeGreaterThan(0)
  }
  const desktopHeights: number[] = []
  for (const width of [1568, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    const currentLayout = await layout()
    expect(currentLayout.columns).toBe(4)
    expect(currentLayout.cardKeys).toEqual(expectedOrder)
    const geometry = await cards.evaluate((grid) => {
      const visibleCards = Array.from(
        grid.querySelectorAll<HTMLElement>(':scope > li[data-card-key]'),
      ).filter((item) => item.getClientRects().length > 0)
      const heights = visibleCards.map((item) => Math.round(item.getBoundingClientRect().height))
      const addTile = grid.querySelector<HTMLElement>(':scope > li[data-analysis-add-card]')!
      return {
        heightCount: new Set(heights).size,
        cardHeight: heights[0],
        addTileHeight: Math.round(addTile.getBoundingClientRect().height),
        addTileLast: grid.lastElementChild?.matches('[data-analysis-add-card]') ?? false,
      }
    })
    expect(geometry.heightCount).toBe(1)
    expect(geometry.addTileLast).toBe(true)
    expect(geometry.addTileHeight).toBeGreaterThanOrEqual(90)
    expect(geometry.addTileHeight).toBeLessThanOrEqual(120)
    expect(geometry.cardHeight).toBeGreaterThan(geometry.addTileHeight)
    desktopHeights.push(geometry.cardHeight!)
    const alignment = await page.evaluate(() => {
      const picker = document.querySelector('.analysis-period-picker')!.getBoundingClientRect()
      const grid = document.querySelector('.analysis-card-list')!.getBoundingClientRect()
      return {
        rightEdge: Math.abs(picker.right - grid.right),
        documentFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      }
    })
    expect(alignment.rightEdge).toBeLessThanOrEqual(1)
    expect(alignment.documentFits).toBe(true)
  }
  expect(Math.abs(desktopHeights[0]! - desktopHeights[1]!)).toBeLessThanOrEqual(1)

  await expect(cards.locator('[data-card-key="weight_kg"], [data-card-key="active_energy_kcal"]')).toHaveCount(0)
  await expect(cards.locator('[data-card-key="weight"] svg[role="img"]')).toBeVisible()
  await expect(cards.locator('[data-card-key="folate_ug"] svg[role="img"]')).toBeVisible()

  const nutrientSummary = cards.locator('[data-card-key="micronutrients"]')
  await expect(nutrientSummary.locator('[data-nutrient-group="vitamin"]')).toContainText(/3\s*(von|\/)\s*3/u)
  await expect(nutrientSummary.locator('[data-nutrient-group="mineral"]')).toContainText(/2\s*(von|\/)\s*2/u)
  await expect(nutrientSummary.locator('.analysis-nutrient-trends > li')).toHaveCount(0)
  await expect(nutrientSummary.locator('.analysis-drag-handle')).toHaveCount(1)
  await expect(nutrientSummary.locator('.analysis-overflow-trigger')).toHaveCount(1)
  const macroKpis = nutrition.locator('.analysis-nutrition-kpi')
  await expect(macroKpis).toHaveCount(3)
  await expect(macroKpis.locator('.analysis-nutrition-kpi-label'))
    .toHaveText(['Eiweiß', 'Kohlenhydrate', 'Fett'])
  const macroValues = macroKpis.locator('.analysis-metric-value')
  await expect(macroValues).toHaveCount(3)
  for (let index = 0; index < 3; index += 1) {
    await expect(macroValues.nth(index)).toBeVisible()
  }
  const macroCharts = nutrition.locator('.analysis-nutrition-macro-trend-chart[role="img"]')
  await expect(macroCharts).toHaveCount(3)
  for (let index = 0; index < 3; index += 1) {
    await expect(macroCharts.nth(index)).toBeVisible()
  }
  for (const key of ['dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g']) {
    await expect(cards.locator(`[data-card-key="${key}"] .analysis-metric-value`)).toBeVisible()
  }
  for (const key of ['protein_g', 'carbohydrates_g', 'fat_g']) {
    await expect(cards.locator(`[data-card-key="${key}"] .analysis-mini-chart`)).toBeVisible()
  }
  const pickerGridOffset = await page.evaluate(() => {
    const picker = document.querySelector('.analysis-period-picker')!.getBoundingClientRect()
    const grid = document.querySelector('.analysis-card-list')!.getBoundingClientRect()
    return Math.abs(picker.right - grid.right)
  })
  expect(pickerGridOffset).toBeLessThanOrEqual(1)
  await expect(page.locator('.analysis-heading-copy p')).toHaveCount(0)
  await page.goto('/analyse/koerper/gewicht')
  const desktopPicker = page.locator('.analysis-period-picker')
  await desktopPicker.locator('.analysis-period-custom-button').click()
  const desktopPickerColumns = await desktopPicker.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(desktopPickerColumns).toBe(2)
  await expect(desktopPicker.locator('.analysis-period-custom-form')).toBeVisible()
  await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')


  for (const [width, columns] of [[500, 1], [430, 1], [390, 1]] as const) {
    await page.setViewportSize({ width, height: 844 })
    const currentLayout = await layout()
    expect(currentLayout.columns).toBe(columns)
    expect(currentLayout.cardWidths.every((width) => width > 0)).toBe(true)
    if (width === 500) expect(Math.min(...currentLayout.cardWidths)).toBeGreaterThanOrEqual(200)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
  }
  await page.goto('/analyse/koerper/gewicht')
  const mobileWeightPicker = page.locator('.analysis-period-picker')
  await mobileWeightPicker.locator('.analysis-period-trigger').click()
  const mobileWeightPickerColumns = await mobileWeightPicker.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/).length,
  )
  expect(mobileWeightPickerColumns).toBe(1)
  await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')
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
    await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')
    if (width === 320) {
      await page.addStyleTag({ content: 'html { scrollbar-gutter: stable; }' })
    }
    const overviewWidth = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }))
    expect(overviewWidth.scrollWidth).toBeLessThanOrEqual(overviewWidth.clientWidth)
    const handle = page.locator('.analysis-card-list [data-card-key="weight"] .analysis-drag-handle')
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
    await page.locator('.analysis-card-list [data-card-key="weight"] .analysis-overflow-trigger').focus()
    await page.keyboard.press('Enter')
    const overflow = page.locator('.analysis-card-list [data-card-key="weight"] .analysis-overflow-menu')
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
      await page.setViewportSize({ width: 390, height: 844 })
      await page.goto(`${path}?period=custom&start=2026-09-22&end=2026-09-28`)
      const chart = page.locator('.chart-card [role="img"]')
      await expect(chart.first()).toBeVisible()
      const canvas = page.locator('.chart-card canvas').first()
      await expect(canvas).toBeVisible()
      const initialCanvasWidth = await canvas.evaluate((element) => element.getBoundingClientRect().width)
      await page.setViewportSize({ width, height: 844 })
      await expect.poll(() => page.evaluate((initialWidth) => {
        const root = document.documentElement
        const detail = document.querySelector('.analysis-detail-page')
        const canvasElement = document.querySelector('.chart-card canvas')
        const chartHost = canvasElement?.closest('.chart-card')?.querySelector('[role="img"]')
        if (!detail || !canvasElement || !chartHost) return null
        const detailBounds = detail.getBoundingClientRect()
        const hostBounds = chartHost.getBoundingClientRect()
        const canvasBounds = canvasElement.getBoundingClientRect()
        return {
          documentFits: root.scrollWidth <= root.clientWidth + 1,
          detailFits: detail.scrollWidth <= detail.clientWidth + 1
            && Array.from(detail.children).every((child) => child.getBoundingClientRect().right <= detailBounds.right + 1),
          canvasFits: hostBounds.width > 0 && hostBounds.height > 0
            && canvasBounds.width >= hostBounds.width * 0.95
            && canvasBounds.width <= hostBounds.width + 1
            && canvasBounds.height >= hostBounds.height * 0.95
            && canvasBounds.height <= hostBounds.height + 1
            && canvasBounds.left >= hostBounds.left - 1
            && canvasBounds.right <= hostBounds.right + 1
            && canvasBounds.top >= hostBounds.top - 1
            && canvasBounds.bottom <= hostBounds.bottom + 1
            && canvasBounds.width < initialWidth - 1
            && canvasBounds.left >= detailBounds.left - 1
            && canvasBounds.right <= detailBounds.right + 1,
        }
      }, initialCanvasWidth)).toEqual({ documentFits: true, detailFits: true, canvasFits: true })
      expect(await canvas.evaluate((element) => {
        const renderedCanvas = element as HTMLCanvasElement
        const context = renderedCanvas.getContext('2d')
        if (!context || renderedCanvas.width === 0 || renderedCanvas.height === 0) return false
        const pixels = context.getImageData(0, 0, renderedCanvas.width, renderedCanvas.height).data
        const background = pixels.slice(0, 4)
        let drawnPixels = 0
        for (let index = 4; index < pixels.length; index += 4) {
          if (pixels[index + 3] > 0 && (
            pixels[index] !== background[0]
            || pixels[index + 1] !== background[1]
            || pixels[index + 2] !== background[2]
            || pixels[index + 3] !== background[3]
          )) {
            drawnPixels += 1
            if (drawnPixels >= 10) return true
          }
        }
        return false
      })).toBe(true)
      for (const surface of await chart.all()) {
        const box = await surface.boundingBox()
        expect(box!.x).toBeGreaterThanOrEqual(0)
        expect(box!.x + box!.width).toBeLessThanOrEqual(width)
        expect(await surface.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true)
      }
      expect(await page.evaluate(() => {
        const root = document.documentElement
        return root.scrollWidth <= root.clientWidth
      })).toBe(true)
      const detailLayout = await page.locator('.analysis-detail-page').evaluate((element) => {
        const bounds = element.getBoundingClientRect()
        return element.scrollWidth > element.clientWidth + 1
          || Array.from(element.children).some((child) => child.getBoundingClientRect().right > bounds.right + 1)
      })
      expect(detailLayout).toBe(false)
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
test('Hub empty states stay content-sized and centered inside fixed cards', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.route(/\/api\/v1\/analytics\/weight(?:\?.*)?$/u, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        start_date: '2026-09-22',
        end_date: '2026-09-28',
        selected_provider: null,
        points: [],
        summary: {
          measurement_count: 0, latest: null, change_kg: null,
          average_kg: null, minimum_kg: null, maximum_kg: null,
        },
      }),
    })
  })
  await page.setViewportSize({ width: 1568, height: 900 })
  await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')

  const emptyCard = page.locator('.analysis-card[data-card-key="weight"]')
  const state = emptyCard.locator('.analysis-card-state')
  await expect(state).toHaveText('Keine Messungen im Zeitraum')
  const geometry = await emptyCard.evaluate((card) => {
    const stateElement = card.querySelector<HTMLElement>('.analysis-card-state')!
    const header = card.querySelector<HTMLElement>('.analysis-card-header')!.getBoundingClientRect()
    const footer = card.querySelector<HTMLElement>('.analysis-card-footer')!.getBoundingClientRect()
    const stateBounds = stateElement.getBoundingClientRect()
    const gap = Number.parseFloat(getComputedStyle(card).rowGap)
    const contentTop = header.bottom + gap
    const contentBottom = footer.top - gap
    return {
      stateHeight: stateBounds.height,
      centerDelta: Math.abs(
        stateBounds.top + stateBounds.height / 2 - (contentTop + contentBottom) / 2,
      ),
    }
  })
  expect(geometry.stateHeight).toBeLessThan(60)
  expect(geometry.centerDelta).toBeLessThanOrEqual(1)
})
test('Hub mini-chart slots reach the footer without resizing cards', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 1568, height: 900 })
  await page.goto('/analyse?period=custom&start=2026-09-22&end=2026-09-28')
  await expect(page.locator('.analysis-card-list')).toHaveCount(1)
  await expect(page.locator('[data-card-key="weight"] .analysis-card-chart')).toBeVisible()
  await expect(page.locator('[data-card-key="nutrition"] .analysis-nutrition-macro-trends')).toBeVisible()

  for (const width of [1568, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => document.fonts.ready)
    const geometry = await page.evaluate(() => {
      const chartGaps = Array.from(document.querySelectorAll<SVGElement>('.analysis-card-list .analysis-card-chart'))
        .filter((chart) => chart.getClientRects().length > 0)
        .map((chart) => {
          const card = chart.closest<HTMLElement>('[data-card-key]')!
          const footer = card.querySelector<HTMLElement>('.analysis-card-footer')!
          return {
            cardKey: card.dataset.cardKey,
            gap: footer.getBoundingClientRect().top - chart.getBoundingClientRect().bottom,
          }
        })
      const nutrition = document.querySelector<HTMLElement>('.analysis-card-list [data-card-key="nutrition"]')!
      const macroTrends = nutrition.querySelector<HTMLElement>('.analysis-nutrition-macro-trends')!
      const nutritionFooter = nutrition.querySelector<HTMLElement>('.analysis-card-footer')!
      const weight = document.querySelector<HTMLElement>('.analysis-card-list [data-card-key="weight"]')!
      const energy = document.querySelector<HTMLElement>('.analysis-card-list [data-card-key="dietary_energy_kcal"]')!
      const visibleCardHeights = Array.from(document.querySelectorAll<HTMLElement>('.analysis-card-list > [data-card-key]'))
        .filter((card) => card.getClientRects().length > 0)
        .map((card) => card.getBoundingClientRect().height)
      return {
        chartGaps,
        nutritionGap: nutritionFooter.getBoundingClientRect().top - macroTrends.getBoundingClientRect().bottom,
        weightCardHeight: weight.getBoundingClientRect().height,
        visibleCardHeights,
        energyFadeStops: Array.from(energy.querySelectorAll('linearGradient stop'), (stop) => ({
          offset: stop.getAttribute('offset'),
          opacity: Number(stop.getAttribute('stop-opacity')),
        })),
      }
    })

    expect(geometry.chartGaps.length).toBeGreaterThan(0)
    for (const { cardKey, gap } of geometry.chartGaps) {
      expect(Math.abs(gap), `${cardKey} chart should meet the footer divider`).toBeLessThanOrEqual(1)
    }
    expect(Math.abs(geometry.nutritionGap), 'Nutrition trends should meet the footer divider').toBeLessThanOrEqual(1)
    expect(geometry.visibleCardHeights.length).toBeGreaterThan(0)
    expect(geometry.visibleCardHeights.every((height) => Math.abs(height - geometry.weightCardHeight) <= 1)).toBe(true)
    expect(geometry.energyFadeStops).toEqual([
      { offset: '0%', opacity: 0.22 },
      { offset: '60%', opacity: 0.2 },
      { offset: '100%', opacity: 0.03 },
    ])
  }
})
test('Analysis Hub uses fixed card geometry', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  const hubPath = '/analyse?period=custom&start=2026-09-22&end=2026-09-28'
  await page.goto(hubPath)

  const list = page.locator('.analysis-card-list')
  const cards = list.locator(':scope > li[data-card-key]')
  const addTile = list.locator(':scope > li[data-analysis-add-card]')
  const expectedOrder = [
    'weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g',
    'carbohydrates_g', 'fiber_g', 'fat_g', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg',
    'folate_ug', 'zinc_mg',
  ]
  const heightsAcrossViewports: number[] = []
  for (const width of [1568, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => document.fonts.ready)
    await expect(list).toHaveCount(1)
    await expect(cards).toHaveCount(expectedOrder.length)
    await expect(addTile).toHaveCount(1)
    const keys = await cards.evaluateAll((items) =>
      items.map((item) => item.getAttribute('data-card-key')))
    expect(keys).toEqual(expectedOrder)
    await expect(list.locator('[data-card-key="weight_kg"], [data-card-key="active_energy_kcal"]')).toHaveCount(0)
    await expect(list.locator('[data-card-key="weight"]')).toHaveCount(1)
    await expect(list.locator('[data-card-key="activity"]')).toHaveCount(1)
    await expect(addTile.getByRole('button', { name: 'Karte hinzufügen' })).toBeVisible()

    const geometry = await page.evaluate(() => {
      const picker = document.querySelector('.analysis-period-picker')!.getBoundingClientRect()
      const grid = document.querySelector('.analysis-card-list')!
      const bounds = grid.getBoundingClientRect()
      const items = Array.from(grid.querySelectorAll<HTMLElement>(':scope > li[data-card-key]'))
        .filter((item) => item.getClientRects().length > 0)
      const heights = items.map((item) => Math.round(item.getBoundingClientRect().height))
      const addTile = grid.querySelector<HTMLElement>(':scope > li[data-analysis-add-card]')!
      const weight = document.querySelector<HTMLElement>('[data-card-key="weight"]')!
      const heading = weight.querySelector<HTMLElement>('.analysis-card-heading')!
      const value = weight.querySelector<HTMLElement>('.analysis-metric-value')!
      const label = weight.querySelector<HTMLElement>('.analysis-card-extra')!
      return {
        columns: getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length,
        pickerOffset: Math.abs(picker.right - bounds.right),
        documentFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
        equalCardHeights: new Set(heights).size === 1,
        cardHeight: heights[0],
        addTileHeight: Math.round(addTile.getBoundingClientRect().height),
        addTileLast: grid.lastElementChild?.matches('[data-analysis-add-card]') ?? false,
        weightSectionCount: document.querySelectorAll('.analysis-weight-section').length,
        descriptionCount: document.querySelectorAll('.analysis-heading-copy p').length,
        titleTag: heading.querySelector('a')?.tagName,
        titleWeight: getComputedStyle(heading).fontWeight,
        valueWeight: getComputedStyle(value).fontWeight,
        labelWeight: getComputedStyle(label).fontWeight,
        linkCount: weight.querySelectorAll('a').length,
        weightCardText: weight.textContent?.replace(/\s+/g, ' ').trim(),
        areaFill: weight.querySelector('.analysis-mini-chart__area')?.getAttribute('fill'),
        gradientCount: weight.querySelectorAll('.analysis-mini-chart linearGradient').length,
      }
    })
    expect(geometry.pickerOffset).toBeLessThanOrEqual(1)
    expect(geometry.documentFits).toBe(true)
    expect(geometry.equalCardHeights).toBe(true)
    expect(geometry.addTileLast).toBe(true)
    expect(geometry.addTileHeight).toBeGreaterThanOrEqual(90)
    expect(geometry.addTileHeight).toBeLessThanOrEqual(120)
    expect(geometry.cardHeight).toBeGreaterThan(geometry.addTileHeight)
    expect(geometry.weightSectionCount).toBe(0)
    expect(geometry.descriptionCount).toBe(0)
    expect(geometry.titleTag).toBe('A')
    expect(geometry.titleWeight).toBe('600')
    expect(geometry.valueWeight).toBe('700')
    expect(geometry.labelWeight).toBe('400')
    expect(geometry.linkCount).toBe(1)
    expect(geometry.weightCardText).toContain('69,5 kg')
    expect(geometry.weightCardText).toContain('2 Messungen')
    expect(geometry.weightCardText).not.toMatch(/Durchschnitt|Minimum|Maximum/)
    expect(geometry.areaFill).toMatch(/^url\(#analysis-mini-area-/)
    expect(geometry.gradientCount).toBe(1)
    heightsAcrossViewports.push(geometry.cardHeight!)
    await expect(list.locator('.analysis-card-details')).toHaveCount(0)
  }
  expect(Math.abs(heightsAcrossViewports[0]! - heightsAcrossViewports[1]!)).toBeLessThanOrEqual(1)

})

test('Weight detail shows readings and keeps the URL-backed 3661-day preset', async ({ page }) => {
  await page.clock.install({ time: new Date('2026-09-30T10:00:00Z') })
  await page.setViewportSize({ width: 1440, height: 900 })
  await installSyntheticAnalysisApi(page)
  await page.goto('/analyse/koerper/gewicht?period=custom&start=2026-09-22&end=2026-09-28')
  await expect(page.locator('.analysis-detail-title')).toContainText('Gewicht')
  await expect(page.locator('.weight-summary-primary')).toContainText('69,5 kg')
  await expect(page.getByRole('img', { name: 'Gewichtsverlauf' })).toBeVisible()
  await expect(page.locator('.weight-summary-secondary article')).toHaveCount(3)
  await expect(page.locator('.weight-summary-secondary')).toContainText('Durchschnitt')
  await expect(page.locator('.weight-summary-secondary')).toContainText('Minimum')
  await expect(page.locator('.weight-summary-secondary')).toContainText('Maximum')
  const readingsToggle = page.getByRole('button', { name: 'Messwerte (2)', exact: true })
  await expect(readingsToggle).toBeVisible()
  await expect(readingsToggle).toHaveAttribute('aria-expanded', 'false')
  await readingsToggle.click()
  await expect(readingsToggle).toHaveAttribute('aria-expanded', 'true')
  const readings = page.getByRole('table', { name: 'Messwerte (2)' }).getByRole('row')
  await expect(readings).toHaveCount(3)
  await expect(readings.nth(1)).toContainText('22.09.2026')
  await expect(readings.nth(1)).toContainText('70 kg')
  await expect(readings.nth(2)).toContainText('28.09.2026')
  await expect(readings.nth(2)).toContainText('69,5 kg')
  const picker = page.getByRole('button', { name: 'Individuell', exact: true })
  await picker.click()
  const options = page.locator('.analysis-period-option')
  await expect(options).toHaveText(['7 Tage', '30 Tage', '90 Tage', '6 Monate', '1 Jahr', 'Alle'])
  const rangeRequest = page.waitForRequest((request) => new URL(request.url()).pathname.endsWith('/analytics/weight'))
  await page.getByRole('button', { name: 'Alle', exact: true }).click()
  const requestUrl = new URL((await rangeRequest).url())
  const start = requestUrl.searchParams.get('start')!
  const end = requestUrl.searchParams.get('end')!
  expect(end).toBe('2026-09-30')
  expect((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1).toBe(3661)
  await expect(page).toHaveURL(/period=3661$/)

  const reloadedRangeRequest = page.waitForRequest((request) => new URL(request.url()).pathname.endsWith('/analytics/weight'))
  await page.reload()
  await expect(page).toHaveURL(/period=3661$/)
  const reloadedRangeUrl = new URL((await reloadedRangeRequest).url())
  expect(reloadedRangeUrl.searchParams.get('start')).toBe(start)
  expect(reloadedRangeUrl.searchParams.get('end')).toBe(end)
  await page.getByRole('button', { name: 'Individuell', exact: true }).click()
  const restoredAllOption = page.getByRole('button', { name: 'Alle', exact: true })
  await expect(restoredAllOption).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Escape')

  await page.goto('/analyse/aktivitaet/aktive-energie?period=30')
  await page.getByRole('button', { name: 'Individuell', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Alle', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.goBack()
  await expect(page).toHaveURL(/period=3661$/)
  await page.getByRole('button', { name: 'Individuell', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Alle', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Escape')
  await page.goForward()
  await expect(page).toHaveURL(/period=30$/)
})

for (const width of [375, 768]) {
  test(`Nutrition overview keeps the carbohydrate KPI label unbroken at ${width}px`, async ({ page }) => {
    await installSyntheticAnalysisApi(page)
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/analyse/ernaehrung?period=custom&start=2026-09-22&end=2026-09-28')

    const kpis = page.locator('.nutrition-overview-kpis')
    const carbohydrateKpi = kpis.locator('.nutrition-overview-kpi').filter({ hasText: 'Kohlenhydrate' })
    await expect(carbohydrateKpi).toBeVisible()
    const layout = await kpis.evaluate((grid) => {
      const cards = Array.from(grid.querySelectorAll<HTMLElement>('.nutrition-overview-kpi'))
      const columns = getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length
      const carbohydrate = cards.find((card) => card.innerText.includes('Kohlenhydrate'))!
      const label = carbohydrate.querySelector<HTMLElement>('.nutrition-overview-kpi-label')!
      const text = Array.from(label.childNodes).find(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim() === 'Kohlenhydrate',
      )!
      const range = document.createRange()
      range.selectNodeContents(text)
      const rowHeights = new Map<number, number>()
      for (const card of cards) {
        const bounds = card.getBoundingClientRect()
        const rowTop = Math.round(bounds.top)
        rowHeights.set(rowTop, Math.max(rowHeights.get(rowTop) ?? 0, bounds.height))
      }
      const textRects = Array.from(range.getClientRects())
      const labelBounds = label.getBoundingClientRect()
      return {
        columns,
        labelLineCount: textRects.length,
        wordFits: textRects.every((rect) => rect.left >= labelBounds.left && rect.right <= labelBounds.right),
        rowHeights: Array.from(rowHeights.values()),
      }
    })

    expect(layout.columns).toBe(2)
    expect(layout.labelLineCount).toBe(1)
    expect(layout.wordFits).toBe(true)
    expect(layout.rowHeights).toHaveLength(2)
    expect(layout.rowHeights.every((height) => height >= 88 && height <= 90)).toBe(true)
    expect(Math.abs(layout.rowHeights[0]! - layout.rowHeights[1]!)).toBeLessThanOrEqual(1)
  })
}
