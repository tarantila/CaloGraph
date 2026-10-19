import { expect, test, type Page } from '@playwright/test'

const DEFAULT_CARDS = ['weight', 'nutrition', 'micronutrients', 'activity']
const initialCards = [...DEFAULT_CARDS, 'protein_g', 'vitamin_a_ug']
const periodCapabilities = { supports_all_data: false, all_data_start_date: null }
const todayInBerlin = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date())
const yesterdayInBerlin = new Date(Date.parse(`${todayInBerlin}T00:00:00Z`) - 86_400_000)
  .toISOString().slice(0, 10)

const registry = [
  ['weight_kg', 'body', 'analytics.weight', 3661, 'available', true],
  ['active_energy_kcal', 'activity', 'analytics.verification.activity', 3661, 'available', true],
  ['steps', 'activity', null, 365, 'coming_soon', false],
  ['vitamin_a_ug', 'micronutrients', 'analytics.nutrition.micronutrients', 365, 'available', true],
  ['calcium_mg', 'micronutrients', 'analytics.nutrition.micronutrients', 365, 'available', true],
  ['iron_mg', 'micronutrients', 'analytics.nutrition.micronutrients', 365, 'available', true],
  ['magnesium_mg', 'micronutrients', 'analytics.nutrition.micronutrients', 365, 'available', true],

  ...['dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g', 'fiber_g', 'sugar_g', 'saturated_fat_g']
    .map((key) => [key, 'nutrition', 'analytics.nutrition.analysis', 365, 'available', true]),
  ['bmi', 'body', null, 365, 'coming_soon', false],
  ['water_intake_ml', 'fluid', null, 365, 'coming_soon', false],
].map(([analysis_key, category_key, reader_key, max_days, status, card_capable]) => ({
  analysis_key, category_key, domain_key: analysis_key, reader_key, status,
  card_capable, detail_capable: card_capable,
  period_capability: card_capable ? { min_days: 1, max_days, custom_range: true } : null,
}))

function mealEvents(date: string, groupCount: number) {
  if (groupCount === 2) {
    return [
      { event_id: 'breakfast-coffee', local_date: date, provider_key: 'yazio', local_time: '08:20:00', meal_type: 'breakfast', display_name: 'Schwarzer Kaffee', dietary_energy_kcal: 30 },
      { event_id: 'lunch-oatly', local_date: date, provider_key: 'yazio', local_time: '12:20:00', meal_type: 'lunch', display_name: 'Oatly Barista Edition Lighter', dietary_energy_kcal: 86 },
      { event_id: 'breakfast-corny', local_date: date, provider_key: 'yazio', local_time: '08:10:00', meal_type: 'breakfast', display_name: 'Corny Protein Erdnuss', dietary_energy_kcal: 20 },
      { event_id: 'breakfast-sweetener', local_date: date, provider_key: 'yazio', local_time: '08:15:00', meal_type: 'breakfast', display_name: 'süssstoff', dietary_energy_kcal: 15 },
    ]
  }
  const types = ['breakfast', 'lunch', 'dinner', 'snack', 'unknown']
  return types.slice(0, groupCount).flatMap((type, groupIndex) =>
    Array.from({ length: groupCount > 1 && groupIndex === 0 ? 2 : 1 }, (_, entryIndex) => ({
      event_id: `${type}-${entryIndex}`,
      local_date: date,
      provider_key: 'yazio',
      local_time: `${String(8 + groupIndex).padStart(2, '0')}:${String(entryIndex * 15).padStart(2, '0')}:00`,
      meal_type: type,
      display_name: `Synthetic ${type} ${entryIndex}`,
      dietary_energy_kcal: 20 + groupIndex * 10 + entryIndex,
    })))
}

function day(date: string, meals = false, groupCount = 2) {
  return {
    date, provider_key: 'yazio', dietary_energy_kcal: 1200,
    protein_g: 60, carbohydrates_g: 140, fat_g: 40, fiber_g: 20, sugar_g: 25, saturated_fat_g: 8,
    meals: meals ? mealEvents(date, groupCount) : [],
  }
}

async function installSyntheticAnalysisApi(
  page: Page,
  options: { language?: 'de' | 'en'; activeCards?: string[]; mealGroupCount?: number } = {},
) {
  const state = {
    activeCards: [...(options.activeCards ?? initialCards)],
    mealGroupCount: options.mealGroupCount ?? 2,
    persistedWrites: [] as string[][],
    mealReads: [] as Array<{ start: string | null; end: string | null }>,
    dailyMealReads: [] as Array<{ date: string | null; view: string | null }>,
  }

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace(/^\/api\/v1/u, '')
    if (path === '/settings/analysis-preferences' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { active_card_ids: string[] }
      expect(Object.keys(body)).toEqual(['active_card_ids'])
      state.persistedWrites.push([...body.active_card_ids])
      state.activeCards = [...body.active_card_ids]
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ active_card_ids: state.activeCards }),
      })
      return
    }
    if (path === '/analytics/nutrition/analysis') {
      const start = url.searchParams.get('start')
      const end = url.searchParams.get('end')
      state.mealReads.push({ start, end })
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({
          start_date: start, end_date: end, selected_provider: { provider_key: 'yazio' },
          period_days: (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1,
          days: [day(end ?? todayInBerlin, end === todayInBerlin, state.mealGroupCount)],
        }),
      })
      return
    }
    if (path === '/analytics/verification/nutrition') {
      const date = url.searchParams.get('date')
      const view = url.searchParams.get('view')
      state.dailyMealReads.push({ date, view })
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ date, view, canonical: null, providers: [] }),
      })
      return
    }

    const start = url.searchParams.get('start') ?? yesterdayInBerlin
    const end = url.searchParams.get('end') ?? todayInBerlin
    const range = { start_date: start, end_date: end, period_capabilities: periodCapabilities }
    const metricKey = url.searchParams.get('metric_key')
    const micronutrients = [
      { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 30, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
      { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: 300, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
      { metric_key: 'iron_mg', label: 'Iron', category: 'mineral', unit: 'mg', eu_nrv: 14, total: 6, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
      { metric_key: 'magnesium_mg', label: 'Magnesium', category: 'mineral', unit: 'mg', eu_nrv: 375, total: 100, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
    ]

    const responses: Record<string, unknown> = {
      '/auth/me': {
        id: 'analysis-hub-canonical-user', username: 'analysis-hub-canonical', language: options.language ?? 'de', timezone: 'Europe/Berlin',

        week_starts_on: 1, preferred_weight_unit: 'kg', raw_payload_retention_days: 0,
        is_admin: false, is_active: true, deactivated_at: null,
      },
      '/settings/onboarding': { mode: 'legacy', required: false, completed: true, current_step: 'completed' },
      '/settings/analysis-preferences': { active_card_ids: state.activeCards },
      '/analytics/registry': { analyses: registry },
      '/analytics/weight': {
        ...range, selected_provider: null,
        points: [
          { date: start, weight_kg: 0 },
          { date: end, weight_kg: -1 },
          { date: end, weight_kg: 69.5 },
        ],
        summary: { measurement_count: 3, latest: { date: end, weight_kg: 69.5 }, change_kg: -0.5, average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70 },
      },
      '/analytics/verification/activity': {
        ...range, view: 'canonical',
        days: [{ date: end, canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 219.8, record_count: 1, source_types: [] }, providers: [] }],
      },
      '/analytics/nutrition/micronutrients': {
        ...range, selected_provider: null, period_days: 7,
        metrics: metricKey ? micronutrients.filter((item) => item.metric_key === metricKey) : micronutrients,
        series: metricKey ? [
          { date: start, provider_key: 'yazio', value: metricKey === 'calcium_mg' ? 100 : 10 },
          { date: end, provider_key: 'yazio', value: metricKey === 'calcium_mg' ? 200 : 20 },
        ] : [],
      },
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses[path] ?? {}) })
  })
  return state
}

async function readCardOrder(page: Page): Promise<string[]> {
  return page.locator('.analysis-card-list > li[data-card-key]').evaluateAll((cards) =>
    cards.map((card) => card.getAttribute('data-card-key')!))
}

async function openHub(page: Page, period = '30', cards: string[] = initialCards) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/analyse?period=${period}`)
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(cards.length)
  await expect.poll(() => readCardOrder(page)).toEqual(cards)
}


async function moveVitaminAUp(page: Page) {
  const vitamin = page.locator('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
  await vitamin.scrollIntoViewIfNeeded()
  await vitamin.locator('.analysis-overflow-trigger').click()
  await vitamin.locator('.analysis-overflow-menu button').first().click()
}

test('uses one persistent mixed card list and deduplicates domain representatives', async ({ page }) => {
  const nutritionSummaryCards = [...DEFAULT_CARDS, 'dietary_energy_kcal', 'protein_g', 'vitamin_a_ug']
  const state = await installSyntheticAnalysisApi(page, { activeCards: nutritionSummaryCards })
  await openHub(page, '30', nutritionSummaryCards)
  await expect(page.getByRole('heading', { name: 'Meine Analysen', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Weitere Analysen', exact: true })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Alle Analysen', exact: true })).toHaveCount(0)
  await expect(page.locator('.analysis-card-list [data-card-key="weight"]')).toHaveCount(1)
  await expect(page.locator('.analysis-card-list [data-card-key="activity"]')).toHaveCount(1)
  await expect(page.locator('.analysis-card-list [data-card-key="weight_kg"], .analysis-card-list [data-card-key="active_energy_kcal"]')).toHaveCount(0)
  const nutritionSummaryCard = page.locator('.analysis-card-list [data-card-key="nutrition"]')
  const macroKpis = nutritionSummaryCard.locator('.analysis-nutrition-kpi')
  await expect(macroKpis).toHaveCount(3)
  await expect(macroKpis.locator('.analysis-nutrition-kpi-label')).toHaveText(['Eiweiß', 'Kohlenhydrate', 'Fett'])
  await expect(macroKpis.locator('.analysis-metric-value')).toHaveText(['60 g', '140 g', '40 g'])
  await expect(page.locator('.analysis-card-list [data-card-key="dietary_energy_kcal"] .analysis-metric-value'))
    .toHaveText('1.200 kcal')
  const macroCharts = nutritionSummaryCard.locator('.analysis-nutrition-macro-trend-chart[role="img"]')
  await expect(macroCharts).toHaveCount(3)
  await expect(macroCharts.nth(0)).toHaveAttribute('aria-label', /Eiweiß/u)
  await expect(macroCharts.nth(1)).toHaveAttribute('aria-label', /Kohlenhydrate/u)
  await expect(macroCharts.nth(2)).toHaveAttribute('aria-label', /Fett/u)
  await expect(macroCharts.nth(0)).toHaveClass(/analysis-mini-chart--teal/u)
  await expect(macroCharts.nth(1)).toHaveClass(/analysis-mini-chart--blue/u)
  await expect(macroCharts.nth(2)).toHaveClass(/analysis-mini-chart--amber/u)

  await moveVitaminAUp(page)
  const mixedOrder = ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'vitamin_a_ug', 'protein_g']
  await expect.poll(() => state.persistedWrites).toEqual([mixedOrder])
  await page.reload()
  await expect.poll(() => readCardOrder(page)).toEqual(mixedOrder)
  await expect(page.locator('.analysis-card-list [data-card-key="vitamin_a_ug"]')).toHaveCount(1)
  expect(await page.locator('.analysis-card-list [data-card-key="weight"] .analysis-metric-value').textContent())
    .toBe('69,5 kg')
  const weightCard = page.locator('.analysis-card-list [data-card-key="weight"]')
  await expect(weightCard.locator('.analysis-mini-chart polyline')).toHaveCount(0)
  await expect(weightCard.locator('.analysis-mini-chart circle')).toHaveCount(1)


})

test('centres the manager and routes Meals Today to its date-specific view', async ({ page }) => {
  const state = await installSyntheticAnalysisApi(page)
  await openHub(page, `custom&start=${yesterdayInBerlin}&end=${yesterdayInBerlin}`)


  const weightCard = page.locator('.analysis-card-list [data-card-key="weight"]')
  const footerCopyBox = await weightCard.locator('.analysis-card-footer-copy').boundingBox()
  const chevronBox = await weightCard.locator('.analysis-card-caret').boundingBox()
  expect(footerCopyBox).not.toBeNull()
  expect(chevronBox).not.toBeNull()
  expect(Math.abs(
    (footerCopyBox!.y + footerCopyBox!.height / 2) - (chevronBox!.y + chevronBox!.height / 2),
  )).toBeLessThanOrEqual(1)

  const addTile = page.locator('.analysis-add-card-button')
  const addTileBox = await addTile.boundingBox()
  const cardBox = await weightCard.boundingBox()
  expect(addTileBox?.height).toBeGreaterThanOrEqual(90)
  expect(addTileBox?.height).toBeLessThanOrEqual(120)
  expect(cardBox?.height).toBeGreaterThan(addTileBox?.height ?? 0)
  await expect(page.locator('.analysis-card-list [data-card-key="vitamin_a_ug"] .analysis-card-icon'))
    .toHaveAttribute('data-icon', 'carrot')

  const trigger = page.getByRole('button', { name: 'Karte hinzufügen', exact: true })
  await page.setViewportSize({ width: 1280, height: 560 })
  await trigger.scrollIntoViewIfNeeded()
  const pageBeforeOpen = await page.evaluate(() => ({
    x: window.scrollX,
    y: window.scrollY,
    rootOverflow: document.documentElement.style.overflow,
    bodyOverflow: document.body.style.overflow,
    bodyPaddingRight: document.body.style.paddingRight,
  }))
  const triggerBeforeOpen = await trigger.boundingBox()
  await trigger.click()
  const dialog = page.getByRole('dialog', { name: 'Karte hinzufügen' })
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Karte hinzufügen', exact: true })).toBeFocused()
  const dialogBox = await dialog.boundingBox()
  expect(dialogBox?.width).toBeGreaterThanOrEqual(640)
  expect(dialogBox?.width).toBeLessThanOrEqual(720)
  const usableLayoutViewportWidth = await page.evaluate(() => document.documentElement.getBoundingClientRect().width)
  const dialogCenter = dialogBox!.x + dialogBox!.width / 2
  expect(Math.abs(dialogCenter - usableLayoutViewportWidth / 2)).toBeLessThanOrEqual(1)
  expect(dialogBox!.y + dialogBox!.height / 2).toBeCloseTo(280, 0)
  expect(dialogBox!.height).toBeLessThanOrEqual(448)
  const triggerWhileOpen = await trigger.boundingBox()
  expect(triggerWhileOpen?.x).toBeCloseTo(triggerBeforeOpen!.x, 0)
  expect(triggerWhileOpen?.y).toBeCloseTo(triggerBeforeOpen!.y, 0)
  expect(await page.evaluate(() => ({
    rootOverflow: getComputedStyle(document.documentElement).overflowY,
    bodyOverflow: getComputedStyle(document.body).overflowY,
  }))).toEqual({ rootOverflow: 'hidden', bodyOverflow: 'hidden' })
  const pageScrollWhileOpen = await page.evaluate(() => window.scrollY)
  await page.mouse.move(80, 80)
  await page.mouse.wheel(0, 500)
  expect(await page.evaluate(() => window.scrollY)).toBe(pageScrollWhileOpen)

  const categoryGrid = dialog.locator('.analysis-manager-category-grid')
  const categories = dialog.locator('.analysis-manager-category-tile')
  await expect(categories).toHaveText(['Körper', 'Aktivität', 'Ernährung', 'Mikronährstoffe', 'Flüssigkeit'])
  expect(await categories.evaluateAll((elements) => elements.map((element) =>
    element.querySelector('.analysis-manager-category-icon')?.getAttribute('data-icon'))))
    .toEqual(['scale', 'activity', 'nutrition', 'atom', 'drop'])
  await expect(categories.locator('.analysis-manager-category-icon svg')).toHaveCount(5)
  expect(await categoryGrid.evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(3)
  expect(await categories.evaluateAll((elements) => elements.map((element) => element.getAttribute('aria-expanded'))))
    .toEqual(['false', 'false', 'false', 'false', 'false'])
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(0)

  const search = dialog.getByRole('searchbox', { name: 'Karten durchsuchen' })
  await expect(search).toHaveAttribute('placeholder', 'Karten durchsuchen …')
  const micronutrientTile = dialog.locator('[data-manager-category="micronutrients"]')
  await micronutrientTile.click()
  await expect(micronutrientTile).toHaveAttribute('aria-expanded', 'true')
  const selectedGroup = dialog.locator('[data-manager-group]')
  await expect(selectedGroup).toHaveCount(1)
  await expect(selectedGroup).toHaveAttribute('data-manager-group', 'micronutrients')
  await expect(dialog.locator('[data-hub-card-option="vitamin_a_ug"]')).toBeVisible()
  await expect(dialog.locator('[data-hub-card-option="weight"]')).toHaveCount(0)

  const managerContent = dialog.locator('.analysis-manager-body')
  const managerScroll = await managerContent.evaluate((element) => ({
    overflowY: getComputedStyle(element).overflowY,
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }))
  expect(managerScroll.overflowY).toBe('auto')
  expect(managerScroll.scrollHeight).toBeGreaterThan(managerScroll.clientHeight)
  const bodyBox = await managerContent.boundingBox()
  const headerBox = await dialog.locator('.analysis-manager-header').boundingBox()
  await page.mouse.move(bodyBox!.x + bodyBox!.width / 2, bodyBox!.y + bodyBox!.height - 12)
  await page.mouse.wheel(0, 300)
  await expect.poll(() => managerContent.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  expect(await page.evaluate(() => window.scrollY)).toBe(pageScrollWhileOpen)
  expect((await dialog.locator('.analysis-manager-header').boundingBox())?.y).toBeCloseTo(headerBox!.y, 0)
  await managerContent.evaluate((element) => { element.scrollTop = 0 })

  await search.fill('PROTEIN')
  await expect(categoryGrid).toHaveCount(0)
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(1)
  await expect(dialog.locator('[data-manager-group]')).toHaveAttribute('data-manager-group', 'nutrition')
  await expect(dialog.locator('[data-hub-card-option="protein_g"]')).toBeVisible()
  await expect(dialog.locator('[data-hub-card-option="weight"]')).toHaveCount(0)
  await expect(dialog.locator('[data-hub-card-option="vitamin_a_ug"]')).toHaveCount(0)

  await search.fill('')
  await expect(categoryGrid).toBeVisible()
  await expect(micronutrientTile).toHaveAttribute('aria-expanded', 'true')
  await expect(dialog.locator('[data-manager-group]')).toHaveAttribute('data-manager-group', 'micronutrients')
  await expect(dialog.locator('[data-hub-card-option="vitamin_a_ug"]')).toBeVisible()
  await micronutrientTile.click()
  await expect(micronutrientTile).toHaveAttribute('aria-expanded', 'false')
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(0)

  await search.fill('BMI')
  await expect(categoryGrid).toHaveCount(0)
  await expect(dialog.locator('[data-manager-group]')).toHaveAttribute('data-manager-group', 'body')
  const unavailableMatch = dialog.locator('[data-hub-card-option="bmi"]')
  await expect(unavailableMatch).toHaveAttribute('aria-disabled', 'true')
  await expect(unavailableMatch.locator('.analysis-manager-unavailable')).toBeVisible()
  await expect(unavailableMatch.locator('button')).toHaveCount(0)
  await search.fill('kein-treffer')
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(0)
  await expect(dialog.locator('.analysis-manager-empty')).toContainText('Keine passenden Analysen gefunden')
  await search.fill('')
  await expect(categoryGrid).toBeVisible()
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(0)
  expect(await categories.evaluateAll((elements) => elements.map((element) => element.getAttribute('aria-expanded'))))
    .toEqual(['false', 'false', 'false', 'false', 'false'])
  const nutritionTile = dialog.locator('[data-manager-category="nutrition"]')
  await nutritionTile.click()
  const nutritionOptions = dialog.locator('[data-manager-group="nutrition"] [data-hub-card-option]')
  expect(await nutritionOptions.evaluateAll((elements) => elements.map((element) =>
    element.getAttribute('data-hub-card-option')))).toEqual([
    'meals_today', 'dietary_energy_kcal', 'carbohydrates_g', 'fat_g', 'fiber_g',
    'sugar_g', 'saturated_fat_g', 'nutrition', 'protein_g',
  ])
  expect(await nutritionOptions.evaluateAll((elements) =>
    elements.map((element) => element.querySelector('button')?.textContent?.trim()))).toEqual([
    'Hinzufügen', 'Hinzufügen', 'Hinzufügen', 'Hinzufügen', 'Hinzufügen',
    'Hinzufügen', 'Hinzufügen', 'Entfernen', 'Entfernen',
  ])
  await nutritionTile.click()
  await expect(dialog.locator('[data-manager-group]')).toHaveCount(0)
  await page.mouse.click(10, 10)
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  const pageAfterOutsideClose = await page.evaluate(() => ({
    x: window.scrollX,
    y: window.scrollY,
    rootOverflow: document.documentElement.style.overflow,
    bodyOverflow: document.body.style.overflow,
    bodyPaddingRight: document.body.style.paddingRight,
  }))
  expect(pageAfterOutsideClose).toEqual(pageBeforeOpen)

  await trigger.click()
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Karte hinzufügen', exact: true })).toBeFocused()
  await dialog.getByRole('heading', { name: 'Karte hinzufügen', exact: true }).click()
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Shift+Tab')
  expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true)
  await dialog.locator('.analysis-manager-close').click()
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()

  await trigger.click()
  await expect(dialog).toBeVisible()
  const scrollYAtMealsManagerOpen = await page.evaluate(() => window.scrollY)
  await dialog.locator('[data-manager-category="nutrition"]').click()
  const mealsRow = dialog.locator('[data-hub-card-option="meals_today"]')
  await mealsRow.getByRole('button', { name: 'Hinzufügen', exact: true }).click()
  await expect.poll(() => state.persistedWrites.at(-1)).toEqual([...initialCards, 'meals_today'])
  await expect(dialog).toBeVisible()
  await expect(mealsRow.locator('.analysis-manager-status')).toBeVisible()
  await expect(mealsRow.getByRole('button', { name: 'Entfernen', exact: true })).toBeFocused()
  const mealsCard = page.locator('.analysis-card-list [data-card-key="meals_today"]')
  await expect(mealsCard).toContainText('Mahlzeiten heute')
  await expect(mealsCard).toContainText('1.200 kcal')
  await expect(mealsCard.locator('.analysis-meal-count')).toHaveText('2 Mahlzeiten · 4 Einträge')
  const mealGroups = mealsCard.locator('.analysis-meal-list > li')
  await expect(mealGroups).toHaveCount(2)
  await expect(mealGroups.locator('.analysis-meal-group-label')).toHaveText(['Frühstück', 'Mittagessen'])
  await expect(mealGroups.locator('.analysis-meal-entry-count')).toHaveText(['3 Einträge', '1 Eintrag'])
  await expect(mealGroups.locator('.analysis-meal-group-kcal')).toHaveText(['65 kcal', '86 kcal'])
  await expect(mealsCard).not.toContainText('Corny Protein Erdnuss')
  await expect(mealsCard).not.toContainText('süssstoff')
  await expect(mealsCard).not.toContainText('Schwarzer Kaffee')
  await expect(mealsCard).not.toContainText('Oatly Barista Edition Lighter')
  expect(state.mealReads).toContainEqual({ start: todayInBerlin, end: todayInBerlin })
  const expectedMealDate = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
  }).format(new Date(`${todayInBerlin}T00:00:00Z`))
  await expect(mealsCard.locator('.analysis-card-footer-copy')).toHaveText(`Heute · ${expectedMealDate}`)
  await expect(mealsCard.locator('.analysis-card-heading a'))
    .toHaveAttribute('href', `/ernaehrung?date=${todayInBerlin}`)

  const searchAgain = dialog.getByRole('searchbox', { name: 'Karten durchsuchen' })
  await searchAgain.fill('Vitamin A')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  const scrollYAfterMealsManagerClose = await page.evaluate(() => window.scrollY)
  expect(scrollYAfterMealsManagerClose).toBe(scrollYAtMealsManagerOpen)
  expect(new URL(page.url()).pathname).toBe('/analyse')
  await expect.poll(() => page.evaluate(() => ({
    x: window.scrollX,
    rootOverflow: document.documentElement.style.overflow,
    bodyOverflow: document.body.style.overflow,
    bodyPaddingRight: document.body.style.paddingRight,
  }))).toEqual({
    x: pageBeforeOpen.x,
    rootOverflow: pageBeforeOpen.rootOverflow,
    bodyOverflow: pageBeforeOpen.bodyOverflow,
    bodyPaddingRight: pageBeforeOpen.bodyPaddingRight,
  })
  expect(state.persistedWrites.at(-1)).toEqual([...initialCards, 'meals_today'])
  await expect.poll(() => readCardOrder(page)).toEqual([...initialCards, 'meals_today'])
  await mealsCard.locator('.analysis-card-heading a').click()
  await expect(page).toHaveURL(new RegExp(`/ernaehrung\\?date=${todayInBerlin}$`, 'u'))
  await expect(page.locator('.verification-date-navigation strong')).toHaveText(expectedMealDate)
  await expect.poll(() => state.dailyMealReads).toContainEqual({ date: todayInBerlin, view: 'canonical' })
  await page.goBack()
  await expect.poll(() => readCardOrder(page)).toEqual([...initialCards, 'meals_today'])

  await page.reload()
  await expect.poll(() => readCardOrder(page)).toEqual([...initialCards, 'meals_today'])
  await trigger.click()
  await expect(dialog).toBeVisible()
  await dialog.locator('[data-manager-category="nutrition"]').click()
  const reloadedMealsRow = dialog.locator('[data-hub-card-option="meals_today"]')
  await reloadedMealsRow.getByRole('button', { name: 'Entfernen', exact: true }).click()
  await expect.poll(() => state.persistedWrites).toEqual([
    [...initialCards, 'meals_today'], initialCards,
  ])
  await expect.poll(() => readCardOrder(page)).toEqual(initialCards)
  await expect(reloadedMealsRow.getByRole('button', { name: 'Hinzufügen', exact: true })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(trigger).toBeFocused()
  await page.reload()
  await expect.poll(() => readCardOrder(page)).toEqual(initialCards)
})

test('keeps meal occasion groups within the card footer for every supported count', async ({ page }) => {
  const cards = [...initialCards, 'meals_today']
  const state = await installSyntheticAnalysisApi(page, { activeCards: cards })
  for (const groupCount of [1, 2, 4, 5, 0]) {
    state.mealGroupCount = groupCount
    await openHub(page, '30', cards)
    const card = page.locator('.analysis-card-list > li[data-card-key="meals_today"]')
    await expect(card.locator('.analysis-metric-value')).toHaveText('1.200 kcal')
    if (groupCount === 0) {
      await expect(card.locator('.analysis-meal-empty')).toHaveText('Keine Ernährungseinträge für heute.')
      await expect(card.locator('.analysis-meal-list')).toHaveCount(0)
      continue
    }

    const groups = card.locator('.analysis-meal-list > li')
    await expect(groups).toHaveCount(groupCount)
    const bounds = await card.evaluate((element) => {
      const list = element.querySelector('.analysis-meal-list')!
      const footer = element.querySelector('.analysis-card-footer')!
      return {
        listBottom: list.getBoundingClientRect().bottom,
        footerTop: footer.getBoundingClientRect().top,
        hasHorizontalOverflow: element.scrollWidth > element.clientWidth,
      }
    })
    expect(bounds.listBottom).toBeLessThanOrEqual(bounds.footerTop)
    expect(bounds.hasHorizontalOverflow).toBe(false)
  }
})


test('formats the Meals Today card date for English users', async ({ page }) => {
  const cards = [...initialCards, 'meals_today']
  await page.clock.install({ time: new Date(`${todayInBerlin}T12:00:00Z`) })
  await installSyntheticAnalysisApi(page, { language: 'en', activeCards: cards })
  await openHub(page, '30', cards)

  const expectedMealDate = new Intl.DateTimeFormat('en-GB', {
    day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
  }).format(new Date(`${todayInBerlin}T00:00:00Z`))
  await expect(page.locator('.analysis-card-list [data-card-key="meals_today"] .analysis-card-footer-copy'))
    .toHaveText(`Today · ${expectedMealDate}`)
})

test('keeps weight samples positive on the compact hub card', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await openHub(page)

  const weight = page.locator('.analysis-card-list > li[data-card-key="weight"]')
  await expect(weight).toContainText('1 Messung')
  await expect(weight.locator('.analysis-card-heading a')).toHaveText('Gewicht')
  await expect(weight.locator('.analysis-card-heading a')).toHaveAttribute('href', /\/analyse\/koerper\/gewicht\?period=30/u)
  await expect(weight.locator('.analysis-card-details')).toHaveCount(0)
  await expect(weight).not.toContainText('0 kg')
  await expect(page.locator('.analysis-weight-section')).toHaveCount(0)
})
