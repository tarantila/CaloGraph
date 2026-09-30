import { expect, test, type Page } from '@playwright/test'

const initialKeys = ['weight_kg', 'active_energy_kcal', 'vitamin_a_ug']
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

async function installSyntheticAnalysisApi(page: Page, rejectSave = false): Promise<{ persistedOrders: string[][] }> {
  const state: { analysisKeys: string[]; persistedOrders: string[][] } = {
    analysisKeys: [...initialKeys],
    persistedOrders: [],
  }
  await page.route('**/api/**', async (route) => {
    const requestUrl = new URL(route.request().url())
    const path = requestUrl.pathname.replace(/^\/api\/v1/u, '')
    if (path === '/settings/analysis-preferences' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { analysis_keys: string[] }
      if (rejectSave) {
        state.persistedOrders.push([...body.analysis_keys])
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic save failure' }) })
        return
      }
      state.analysisKeys = [...body.analysis_keys]
      state.persistedOrders.push([...body.analysis_keys])
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ analysis_keys: state.analysisKeys }),
      })
      return
    }
    const responses: Record<string, unknown> = {
      '/auth/me': {
        id: 'analysis-hub-drag-user', username: 'analysis-hub-drag', language: 'de', timezone: 'Europe/Berlin',
        week_starts_on: 1, preferred_weight_unit: 'kg', raw_payload_retention_days: 0,
        is_admin: false, is_active: true, deactivated_at: null,
      },
      '/settings/onboarding': { mode: 'legacy', required: false, completed: true, current_step: 'completed' },
      '/achievements/reconcile': { achievements: [], newly_unlocked: [] },
      '/settings/analysis-preferences': { analysis_keys: state.analysisKeys },
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
  return state
}

async function openHub(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/analyse')
  await expect(page.locator('.analysis-card')).toHaveCount(3)
}

async function dispatchPointer(
  page: Page,
  type: 'pointermove' | 'pointerup' | 'pointercancel',
  pointerId: number,
  pointerType: 'mouse' | 'touch',
  x: number,
  y: number,
): Promise<void> {
  await page.evaluate(({ type, pointerId, pointerType, x, y }) => {
    window.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId, pointerType, isPrimary: true, button: 0,
      clientX: x, clientY: y,
    }))
  }, { type, pointerId, pointerType, x, y })
}

test('Analysis hub pointer drag persists its destination once without opening a detail', async ({ page }) => {
  const { persistedOrders } = await installSyntheticAnalysisApi(page)
  await openHub(page)

  const weightCard = page.locator('.analysis-card[data-analysis-key="weight_kg"]')
  const targetCard = page.locator('.analysis-card[data-analysis-key="active_energy_kcal"]')
  const handle = weightCard.locator('.analysis-drag-handle')
  const sourceBox = await weightCard.boundingBox()
  const handleBox = await handle.boundingBox()
  const targetBox = await targetCard.boundingBox()
  expect(sourceBox).not.toBeNull()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  const startX = handleBox!.x + handleBox!.width / 2
  const startY = handleBox!.y + handleBox!.height / 2
  const grabOffsetX = startX - sourceBox!.x
  await page.mouse.move(startX, startY)
  await page.mouse.down()
  await expect(weightCard).toHaveClass(/is-dragging/u)
  const dropX = targetBox!.x + targetBox!.width * 0.82
  const dropY = targetBox!.y + targetBox!.height / 2
  await page.mouse.move(dropX, dropY, { steps: 8 })
  await expect(targetCard).toHaveClass(/is-drop-after/u)
  await expect(page.locator('.analysis-card').nth(0)).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
  await page.waitForTimeout(260)
  const shiftedTargetBox = await targetCard.boundingBox()
  expect(shiftedTargetBox).not.toBeNull()
  expect(shiftedTargetBox!.x).toBeLessThan(targetBox!.x - targetBox!.width / 2)
  const draggedBox = await weightCard.boundingBox()
  expect(draggedBox).not.toBeNull()
  expect(Math.abs(draggedBox!.x + grabOffsetX - dropX)).toBeLessThan(18)

  const dragFeedback = await weightCard.evaluate((card) => ({
    translate: getComputedStyle(card).translate,
    scale: getComputedStyle(card).scale,
    pointerEvents: getComputedStyle(card).pointerEvents,
  }))
  expect(dragFeedback.translate).not.toBe('none')
  expect(dragFeedback.scale).toBe('1.025')
  expect(dragFeedback.pointerEvents).toBe('none')
  const insertionMarker = await targetCard.evaluate((card) => {
    const marker = getComputedStyle(card, '::after')
    return {
      width: Number.parseFloat(marker.width),
      height: Number.parseFloat(marker.height),
      borderStyle: marker.borderTopStyle,
    }
  })
  expect(insertionMarker.width).toBeGreaterThan(targetBox!.width * 0.7)
  expect(insertionMarker.height).toBeCloseTo(3, 0)
  expect(insertionMarker.borderStyle).not.toBe('dashed')
  await page.mouse.up()

  await expect.poll(() => persistedOrders.length).toBe(1)
  expect(persistedOrders[0]).toEqual(['active_energy_kcal', 'weight_kg', 'vitamin_a_ug'])
  await expect(page.locator('.analysis-card').nth(0)).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
  await expect(weightCard).toHaveClass(/is-settled/u)
  await expect(page).toHaveURL(/\/analyse(?:\?|$)/u)
  await page.reload()
  await expect(page.locator('.analysis-card').first()).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
  expect(persistedOrders).toHaveLength(1)
})

test('Analysis hub supports touch-handle dragging and keyboard menu reordering', async ({ page }) => {
  const { persistedOrders } = await installSyntheticAnalysisApi(page)
  await openHub(page)

  const weightCard = page.locator('.analysis-card[data-analysis-key="weight_kg"]')
  const targetCard = page.locator('.analysis-card[data-analysis-key="active_energy_kcal"]')
  const handle = weightCard.locator('.analysis-drag-handle')
  expect(await handle.evaluate((element) => getComputedStyle(element).touchAction)).toBe('none')
  await page.setViewportSize({ width: 390, height: 1200 })
  const columns = await page.locator('.analysis-card-list')
    .evaluate((grid) => getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length)
  expect(columns).toBe(1)
  const handleBox = await handle.boundingBox()
  const targetBox = await targetCard.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  const touch = await page.context().newCDPSession(page)
  await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
  const startX = handleBox!.x + handleBox!.width / 2
  const startY = handleBox!.y + handleBox!.height / 2
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x: startX, y: startY, id: 1 }],
  })
  await expect(weightCard).toHaveClass(/is-dragging/u)
  const dropX = targetBox!.x + targetBox!.width / 2
  const dropY = targetBox!.y + targetBox!.height * 0.82
  const hoveredKey = await page.evaluate(({ x, y }) => (
    document.elementFromPoint(x, y)?.closest<HTMLElement>('.analysis-card')?.dataset.analysisKey
  ), { x: dropX, y: dropY })
  expect(hoveredKey).toBe('active_energy_kcal')
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x: dropX, y: dropY, id: 1 }],
  })
  await expect(targetCard).toHaveClass(/is-drop-after/u)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect.poll(() => persistedOrders.length).toBe(1)
  expect(persistedOrders[0]).toEqual(['active_energy_kcal', 'weight_kg', 'vitamin_a_ug'])
  await expect(page.locator('.analysis-card').first()).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
  await touch.detach()
  await expect(handle).toBeEnabled()
  const vitaminCard = page.locator('.analysis-card[data-analysis-key="vitamin_a_ug"]')
  await vitaminCard.locator('.analysis-overflow summary').focus()
  await page.keyboard.press('Enter')
  const moveForward = page.getByRole('button', { name: /Vitamin A nach vorne verschieben/u })
  await expect(moveForward).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(moveForward).toBeFocused()
  await page.keyboard.press('Enter')
  await expect.poll(() => persistedOrders.length).toBe(2)
  expect(persistedOrders[1]).toEqual(['active_energy_kcal', 'vitamin_a_ug', 'weight_kg'])
  expect(await page.locator('.analysis-card').evaluateAll((cards) => cards.map((card) => card.getAttribute('data-analysis-key'))))
    .toEqual(['active_energy_kcal', 'vitamin_a_ug', 'weight_kg'])
  await expect(page).toHaveURL(/\/analyse(?:\?|$)/u)
  await page.reload()
  await expect(page.locator('.analysis-card').first()).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
  expect(await page.locator('.analysis-card').evaluateAll((cards) => cards.map((card) => card.getAttribute('data-analysis-key'))))
    .toEqual(['active_energy_kcal', 'vitamin_a_ug', 'weight_kg'])
  expect(persistedOrders).toHaveLength(2)
})

test('Analysis hub remains touch-scrollable outside the drag handle', async ({ page }) => {
  const { persistedOrders } = await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/analyse')
  const content = page.locator('.analysis-card[data-analysis-key="weight_kg"] .analysis-card-content')
  await expect(content).toBeVisible()
  const box = await content.boundingBox()
  expect(box).not.toBeNull()
  const touch = await page.context().newCDPSession(page)
  await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
  const x = box!.x + box!.width / 2
  const startY = box!.y + box!.height / 2
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ x, y: startY, id: 1 }],
  })
  await touch.send('Input.dispatchTouchEvent', {
    type: 'touchMove',
    touchPoints: [{ x, y: startY - 160, id: 1 }],
  })
  await new Promise((resolve) => setTimeout(resolve, 120))
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  await expect(page.locator('.analysis-card.is-dragging')).toHaveCount(0)
  expect(persistedOrders).toEqual([])
  await expect(page).toHaveURL(/\/analyse(?:\?|$)/u)
  expect(await page.locator('.analysis-card').evaluateAll((cards) => cards.map((card) => card.getAttribute('data-analysis-key'))))
    .toEqual(initialKeys)
  await touch.detach()
})

test('Analysis hub drag feedback respects reduced motion while preserving pointer interaction', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openHub(page)

  const weightCard = page.locator('.analysis-card[data-analysis-key="weight_kg"]')
  const handle = weightCard.locator('.analysis-drag-handle')
  const handleBox = await handle.boundingBox()
  expect(handleBox).not.toBeNull()
  const pointerId = 58
  const startX = handleBox!.x + handleBox!.width / 2
  const startY = handleBox!.y + handleBox!.height / 2
  await handle.dispatchEvent('pointerdown', {
    bubbles: true, cancelable: true, pointerId, pointerType: 'mouse', isPrimary: true,
    button: 0, clientX: startX, clientY: startY,
  })
  await expect(weightCard).toHaveClass(/is-dragging/u)
  await dispatchPointer(page, 'pointermove', pointerId, 'mouse', startX + 80, startY + 24)
  const reducedFeedback = await weightCard.evaluate((card) => ({
    translate: getComputedStyle(card).translate,
    scale: getComputedStyle(card).scale,
    transitionDuration: getComputedStyle(card).transitionDuration,
  }))
  expect(reducedFeedback.translate).not.toBe('none')
  expect(reducedFeedback.scale).toBe('1')
  expect(reducedFeedback.transitionDuration.split(',').every((duration) => Number.parseFloat(duration) === 0)).toBe(true)
  await dispatchPointer(page, 'pointerup', pointerId, 'mouse', startX + 80, startY + 24)
})

test('card header reading order and touch target keep reorder separate from real detail navigation', async ({ page }) => {
  const { persistedOrders } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  const card = page.locator('[data-analysis-card="weight_kg"]')
  const handle = card.locator('.analysis-drag-handle')
  const box = await handle.boundingBox()
  expect(box!.width).toBeGreaterThanOrEqual(44)
  expect(box!.height).toBeGreaterThanOrEqual(44)
  expect(await card.evaluate((element) => {
    const items = ['.analysis-drag-handle', '.analysis-card-icon', 'h3', '.analysis-overflow summary']
      .map((selector) => element.querySelector(selector)!)
    return items.slice(1).every((item, index) => Boolean(items[index]!.compareDocumentPosition(item) & Node.DOCUMENT_POSITION_FOLLOWING))
  })).toBe(true)
  await expect(handle).toHaveAccessibleName(/Gewicht/u)
  await expect(card.locator('.analysis-overflow summary')).toHaveAccessibleName(/Gewicht/u)
  await handle.click()
  await expect(page).toHaveURL(/\/analyse(?:\?|$)/u)
  expect(persistedOrders).toEqual([])
  await card.locator('.analysis-card-content').click()
  await expect(page).toHaveURL(/\/analyse\/koerper\/gewicht/u)
  await expect(page.locator('.weight-summary-primary')).toContainText('69,5 kg')
  await page.goto('/analyse')
  await page.locator('[data-analysis-card="active_energy_kcal"] .analysis-card-content').click()
  await expect(page).toHaveURL(/\/analyse\/aktivitaet\/aktive-energie/u)
  await expect(page.locator('.activity-reading-list')).toContainText('240 kcal')
})

for (const outcome of ['cancel', 'outside', 'reject'] as const) {
  test(`pointer ${outcome} restores original order without success confirmation or detail click-through`, async ({ page }) => {
    const { persistedOrders } = await installSyntheticAnalysisApi(page, outcome === 'reject')
    await openHub(page)
    const weight = page.locator('[data-analysis-card="weight_kg"]')
    const target = page.locator('[data-analysis-card="active_energy_kcal"]')
    const handleBox = await weight.locator('.analysis-drag-handle').boundingBox()
    const targetBox = await target.boundingBox()
    await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
    await page.mouse.down()
    await page.mouse.move(targetBox!.x + targetBox!.width * .82, targetBox!.y + targetBox!.height / 2, { steps: 8 })
    await expect(page.locator('.analysis-card').first()).toHaveAttribute('data-analysis-key', 'active_energy_kcal')
    if (outcome === 'cancel') {
      await dispatchPointer(page, 'pointercancel', 1, 'mouse', 0, 0)
    } else if (outcome === 'outside') {
      await page.mouse.move(2, 2)
    }
    await page.mouse.up()
    await expect.poll(() => page.locator('.analysis-card').evaluateAll((cards) => cards.map((card) => card.getAttribute('data-analysis-key'))))
      .toEqual(initialKeys)
    expect(persistedOrders).toHaveLength(outcome === 'reject' ? 1 : 0)
    await expect(page.locator('[data-drop-state="dropped"], .analysis-card.is-settled')).toHaveCount(0)
    await expect(page).toHaveURL(/\/analyse(?:\?|$)/u)
    if (outcome === 'reject') await expect(page.locator('.analysis-save-error')).toBeVisible()
    await page.reload()
    await expect(page.locator('.analysis-card').first()).toHaveAttribute('data-analysis-key', 'weight_kg')
  })
}
