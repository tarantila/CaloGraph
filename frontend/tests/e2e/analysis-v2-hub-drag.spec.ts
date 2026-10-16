import { expect, test, type Page } from '@playwright/test'
const initialCards = ['weight', 'vitamin_a_ug', 'protein_g', 'nutrition', 'calcium_mg', 'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g']

const metricBeforeBuiltInOrder = ['vitamin_a_ug', 'weight', 'protein_g', 'nutrition', 'calcium_mg', 'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g']
const periodCapabilities = { supports_all_data: false, all_data_start_date: null }
const registry = [
  ['weight_kg', 'body', 'analytics.weight', 3661],
  ['active_energy_kcal', 'activity', 'analytics.verification.activity', 3661],
  ['vitamin_a_ug', 'micronutrients', 'analytics.nutrition.micronutrients', 365],
  ['calcium_mg', 'micronutrients', 'analytics.nutrition.micronutrients', 365],
  ...['dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g'].map((key) => [key, 'nutrition', 'analytics.nutrition.analysis', 365]),
].map(([analysis_key, category_key, reader_key, max_days]) => ({
  analysis_key, category_key, domain_key: analysis_key, reader_key, status: 'available',
  card_capable: true, detail_capable: true,
  period_capability: { min_days: 1, max_days, custom_range: true },
}))

async function installSyntheticAnalysisApi(page: Page, rejectSave = false) {
  const state = {
    activeCards: [...initialCards],
    persistedWrites: [] as Array<{ active_card_ids: string[] }>,
  }
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace(/^\/api\/v1/u, '')
    if (path === '/settings/analysis-preferences' && route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as { active_card_ids: string[] }
      expect(Object.keys(body)).toEqual(['active_card_ids'])
      state.persistedWrites.push({ active_card_ids: [...body.active_card_ids] })
      if (rejectSave) {
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic save failure' }) })
        return
      }
      state.activeCards = [...body.active_card_ids]
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ active_card_ids: state.activeCards }) })
      return
    }
    const range = {
      start_date: url.searchParams.get('start') ?? '2026-09-22',
      end_date: url.searchParams.get('end') ?? '2026-09-28',
      period_capabilities: periodCapabilities,
    }
    const metrics = [
      { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 30, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
      { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: 300, days_with_data: 2, contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
    ]
    const metricKey = url.searchParams.get('metric_key')
    const responses: Record<string, unknown> = {
      '/auth/me': {
        id: 'analysis-hub-drag-user', username: 'analysis-hub-drag', language: 'de', timezone: 'Europe/Berlin',
        week_starts_on: 1, preferred_weight_unit: 'kg', raw_payload_retention_days: 0,
        is_admin: false, is_active: true, deactivated_at: null,
      },
      '/settings/onboarding': { mode: 'legacy', required: false, completed: true, current_step: 'completed' },
      '/achievements/reconcile': { achievements: [], newly_unlocked: [] },
      '/settings/analysis-preferences': {
        active_card_ids: state.activeCards,
      },
      '/analytics/registry': { analyses: registry },
      '/analytics/weight': {
        ...range, selected_provider: null,
        points: [{ date: range.start_date, weight_kg: 70 }, { date: range.end_date, weight_kg: 69.5 }],
        summary: { measurement_count: 2, latest: { date: range.end_date, weight_kg: 69.5 }, change_kg: -0.5, average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70 },
      },
      '/analytics/verification/activity': {
        ...range, view: 'canonical',
        days: [{ date: range.end_date, canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 219.8, record_count: 1, source_types: [] }, providers: [] }],
      },
      '/analytics/nutrition/analysis': {
        ...range, selected_provider: { provider_key: 'yazio' }, period_days: 7,
        days: [{ date: range.start_date, provider_key: 'yazio', dietary_energy_kcal: 410, protein_g: 20, carbohydrates_g: 50, fat_g: 10, fiber_g: 4, sugar_g: 6, saturated_fat_g: 2, meals: [] }],
      },
      '/analytics/nutrition/micronutrients': {
        ...range, selected_provider: null, period_days: 7,
        metrics: metricKey ? metrics.filter((metric) => metric.metric_key === metricKey) : metrics,
        series: metricKey ? [
          { date: range.start_date, provider_key: 'yazio', value: metricKey === 'calcium_mg' ? 100 : 10 },
          { date: range.end_date, provider_key: 'yazio', value: metricKey === 'calcium_mg' ? 200 : 20 },
        ] : [],
      },
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(responses[path] ?? {}) })
  })
  return { persistedWrites: state.persistedWrites, state }
}

function hubCard(page: Page, key: string) {
  return page.locator(`.analysis-card-list > li[data-card-key="${key}"]`)
}

async function cardOrder(page: Page) {
  return page.locator('.analysis-card-list > li[data-card-key]').evaluateAll((cards) =>
    cards.map((card) => card.getAttribute('data-card-key')!))
}

async function openHub(page: Page) {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/analyse?period=7')
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(initialCards.length)
  await expect.poll(() => cardOrder(page)).toEqual(initialCards)
}

async function dispatchPointer(page: Page, type: 'pointermove' | 'pointerup' | 'pointercancel', pointerId: number, x: number, y: number) {
  await page.evaluate(({ type, pointerId, x, y }) => {
    window.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId, pointerType: 'mouse', isPrimary: true, button: 0, clientX: x, clientY: y }))
  }, { type, pointerId, x, y })
}

async function startDrag(
  page: Page,
  sourceKey: string,
  targetKey: string,
  expectedOrder?: string[],
) {
  const source = hubCard(page, sourceKey)
  await source.scrollIntoViewIfNeeded()
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const target = hubCard(page, targetKey)
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(targetBox!.x + targetBox!.width * .2, targetBox!.y + targetBox!.height * .2, { steps: 8 })
  if (expectedOrder) await expect.poll(() => cardOrder(page)).toEqual(expectedOrder)
  return { source, target, targetBox: targetBox! }
}

test('Built-in pointer drag lifts and displaces neighbors without a drop marker or placeholder', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  expect(persistedWrites).toEqual([])
  const { source, target, targetBox } = await startDrag(page, 'weight', 'vitamin_a_ug')
  await expect.poll(() => cardOrder(page)).not.toEqual(initialCards)
  const expectedOrder = await cardOrder(page)
  await expect.poll(async () => {
    const shifted = await target.boundingBox()
    return Math.abs(shifted!.x - targetBox.x) + Math.abs(shifted!.y - targetBox.y)
  }).toBeGreaterThan(targetBox.width / 2)
  const feedback = await source.evaluate((card) => {
    const style = getComputedStyle(card)
    return { translate: style.translate, scale: Number.parseFloat(style.scale), pointerEvents: style.pointerEvents, shadow: style.boxShadow }
  })
  expect(feedback.translate).not.toBe('none')
  expect(feedback.scale).toBeGreaterThan(1)
  expect(feedback.pointerEvents).toBe('none')
  expect(feedback.shadow).not.toBe('none')
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(initialCards.length)
  await expect(page.locator('.analysis-card-list [aria-hidden="true"].analysis-drop-placeholder, .analysis-card-list .analysis-drag-placeholder')).toHaveCount(0)
  const paintedMarkers = await page.locator('.analysis-card-list > li').evaluateAll((cards) => cards.flatMap((card) => ['::before', '::after'].filter((pseudo) => {
    const style = getComputedStyle(card, pseudo)
    const generated = style.content !== 'none' && style.content !== 'normal'
    const color = style.backgroundColor !== 'transparent' && style.backgroundColor !== 'rgba(0, 0, 0, 0)'
    return generated && color && Number.parseFloat(style.width) > 20 && Number.parseFloat(style.height) > 0 && Number.parseFloat(style.height) <= 5
  })))
  expect(paintedMarkers).toEqual([])
  expect(persistedWrites).toEqual([])
  await page.mouse.up()
  await expect.poll(() => persistedWrites).toEqual([{
    active_card_ids: expectedOrder,
  }])
  expect(state.activeCards).toEqual(expectedOrder)
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(expectedOrder)
})
test('reorders when the dragged card footprint enters same-row, lower-row and diagonal targets', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })
  const moves = [
    { targetKey: 'vitamin_a_ug', expected: metricBeforeBuiltInOrder },
    {
      targetKey: 'calcium_mg',
      expected: ['vitamin_a_ug', 'protein_g', 'nutrition', 'calcium_mg', 'weight', 'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g'],
    },
    {
      targetKey: 'dietary_energy_kcal',
      expected: ['vitamin_a_ug', 'protein_g', 'nutrition', 'calcium_mg', 'dietary_energy_kcal', 'weight', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g'],
    },
  ]

  for (const move of moves) {
    state.activeCards = [...initialCards]
    persistedWrites.length = 0
    await page.reload()
    await expect.poll(() => cardOrder(page)).toEqual(initialCards)

    const source = hubCard(page, 'weight')
    const target = hubCard(page, move.targetKey)
    await source.scrollIntoViewIfNeeded()
    const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
    const targetBox = await target.boundingBox()
    expect(handleBox).not.toBeNull()
    expect(targetBox).not.toBeNull()
    await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
    await page.mouse.down()
    await expect(source).toHaveClass(/is-dragging/u)
    await page.mouse.move(targetBox!.x + targetBox!.width * .1, targetBox!.y + targetBox!.height * .1, { steps: 12 })
    await expect.poll(() => cardOrder(page)).toEqual(move.expected)

    const draggedBox = await source.boundingBox()
    expect(draggedBox).not.toBeNull()
    const overlapWidth = Math.max(0, Math.min(draggedBox!.x + draggedBox!.width, targetBox!.x + targetBox!.width) - Math.max(draggedBox!.x, targetBox!.x))
    const overlapHeight = Math.max(0, Math.min(draggedBox!.y + draggedBox!.height, targetBox!.y + targetBox!.height) - Math.max(draggedBox!.y, targetBox!.y))
    expect(overlapWidth * overlapHeight / (targetBox!.width * targetBox!.height)).toBeLessThan(.5)

    await page.mouse.up()
    await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: move.expected }])
  }
})

test('reverses an early reorder when a tall card returns across a shorter card slot', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 500, height: 900 })
  await page.addStyleTag({
    content: `
      .analysis-card-list > li[data-card-key="weight"] { height: 420px !important; }
      .analysis-card-list > li[data-card-key="vitamin_a_ug"] { height: 130px !important; }
    `,
  })

  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'vitamin_a_ug')
  const sourceBox = await source.boundingBox()
  const targetBox = await target.boundingBox()
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  expect(sourceBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  expect(handleBox).not.toBeNull()
  expect(sourceBox!.height).toBeGreaterThan(targetBox!.height * 3)
  expect(Math.abs(sourceBox!.x - targetBox!.x)).toBeLessThan(1)

  const start = {
    x: handleBox!.x + handleBox!.width / 2,
    y: handleBox!.y + handleBox!.height / 2,
  }
  const dragBottomAtStart = sourceBox!.y - 6 + sourceBox!.height
  const pointerYAfterContact = start.y + targetBox!.y - dragBottomAtStart + 2

  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(start.x, pointerYAfterContact, { steps: 2 })
  await expect.poll(() => cardOrder(page)).toEqual([
    'vitamin_a_ug', 'weight', 'protein_g', 'nutrition', 'calcium_mg',
    'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g',
  ])

  await page.waitForTimeout(300)

  const displacedTarget = await target.boundingBox()
  expect(displacedTarget).not.toBeNull()
  expect(pointerYAfterContact).toBeGreaterThan(displacedTarget!.y)
  expect(pointerYAfterContact).toBeLessThan(displacedTarget!.y + displacedTarget!.height)

  await page.mouse.move(start.x, start.y, { steps: 6 })
  await expect.poll(() => cardOrder(page)).toEqual(initialCards)
  await page.mouse.up()
})

test('keeps a diagonal destination when crossing the origin column on a lower row', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })
  await page.waitForTimeout(300)

  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'activity')
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  const start = {
    x: handleBox!.x + handleBox!.width / 2,
    y: handleBox!.y + handleBox!.height / 2,
  }
  expect(targetBox!.y).toBeGreaterThan(start.y + handleBox!.height)

  await page.mouse.move(start.x, start.y)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(targetBox!.x + targetBox!.width * .1, targetBox!.y + targetBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).not.toEqual(initialCards)
  await page.waitForTimeout(300)

  const lowerLeftCard = hubCard(page, 'dietary_energy_kcal')
  const lowerLeftBox = await lowerLeftCard.boundingBox()
  expect(lowerLeftBox).not.toBeNull()
  expect(lowerLeftBox!.y).toBeGreaterThan(start.y + handleBox!.height)
  await page.mouse.move(lowerLeftBox!.x + lowerLeftBox!.width * .1, lowerLeftBox!.y + lowerLeftBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).not.toEqual(initialCards)
  await page.mouse.up()
  await expect.poll(() => persistedWrites).toHaveLength(1)
  expect(persistedWrites[0]!.active_card_ids).not.toEqual(initialCards)
})

test('does not restore the original slot when scrolling makes a lower row cross the origin bounds', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })
  await page.waitForTimeout(300)

  const source = hubCard(page, 'weight')
  const sameRowTarget = hubCard(page, 'vitamin_a_ug')
  const lowerRowTarget = hubCard(page, 'calcium_mg')
  const sourceBox = await source.boundingBox()
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const sameRowBox = await sameRowTarget.boundingBox()
  expect(sourceBox).not.toBeNull()
  expect(handleBox).not.toBeNull()
  expect(sameRowBox).not.toBeNull()
  const origin = {
    x: handleBox!.x + handleBox!.width / 2,
    y: handleBox!.y + handleBox!.height / 2,
  }

  await page.mouse.move(origin.x, origin.y)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(sameRowBox!.x + sameRowBox!.width * .1, sameRowBox!.y + sameRowBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).toEqual(metricBeforeBuiltInOrder)
  await page.waitForTimeout(300)

  const scrollDistance = sourceBox!.height + 18
  await page.evaluate((distance) => {
    if (document.scrollingElement) document.scrollingElement.scrollTop += distance
  }, scrollDistance)
  await expect.poll(() => page.evaluate(() => document.scrollingElement?.scrollTop ?? 0)).toBeGreaterThan(0)

  const lowerRowBox = await lowerRowTarget.boundingBox()
  expect(lowerRowBox).not.toBeNull()
  const destination = {
    x: origin.x,
    y: lowerRowBox!.y + lowerRowBox!.height * .1,
  }
  expect(destination.x).toBeGreaterThanOrEqual(lowerRowBox!.x)
  expect(destination.x).toBeLessThan(lowerRowBox!.x + lowerRowBox!.width)
  expect(destination.y).toBeGreaterThanOrEqual(sourceBox!.y)
  expect(destination.y).toBeLessThan(sourceBox!.y + sourceBox!.height)
  expect(destination.y).toBeGreaterThan(sourceBox!.y + sourceBox!.height - scrollDistance)

  await page.mouse.move(destination.x, destination.y)
  await expect.poll(() => cardOrder(page)).not.toEqual(initialCards)
  await page.mouse.up()
  await expect.poll(() => persistedWrites).toHaveLength(1)
  expect(persistedWrites[0]!.active_card_ids).not.toEqual(initialCards)

})

test('continues to the destination after restoring the origin in one pointer move', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })
  await page.waitForTimeout(300)

  const source = hubCard(page, 'vitamin_a_ug')
  const rightTarget = hubCard(page, 'protein_g')
  const firstTarget = hubCard(page, 'weight')
  const sourceBox = await source.boundingBox()
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const rightBox = await rightTarget.boundingBox()
  expect(sourceBox).not.toBeNull()
  expect(handleBox).not.toBeNull()
  expect(rightBox).not.toBeNull()
  const origin = {
    x: handleBox!.x + handleBox!.width / 2,
    y: handleBox!.y + handleBox!.height / 2,
  }

  await page.mouse.move(origin.x, origin.y)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(rightBox!.x + rightBox!.width / 2, rightBox!.y + rightBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).toEqual([
    'weight', 'protein_g', 'vitamin_a_ug', 'nutrition', 'calcium_mg',
    'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g',
  ])
  await page.waitForTimeout(300)

  const firstBox = await firstTarget.boundingBox()
  expect(firstBox).not.toBeNull()
  const destination = {
    x: firstBox!.x + firstBox!.width / 2,
    y: firstBox!.y + firstBox!.height * .1,
  }
  expect(destination.x).toBeLessThan(origin.x)
  expect(destination.y).toBeGreaterThanOrEqual(sourceBox!.y)
  expect(destination.y).toBeLessThan(sourceBox!.y + sourceBox!.height)

  await page.mouse.move(destination.x, destination.y)
  await expect.poll(() => cardOrder(page)).toEqual(metricBeforeBuiltInOrder)
  await page.mouse.up()
  await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: metricBeforeBuiltInOrder }])
})

test('re-measures Hub drop targets after page scrolling during drag', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })

  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'dietary_energy_kcal')
  await source.scrollIntoViewIfNeeded()
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  expect(handleBox).not.toBeNull()
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)

  await page.evaluate(() => {
    if (document.scrollingElement) document.scrollingElement.scrollTop += 120
  })
  await expect.poll(() => page.evaluate(() => document.scrollingElement?.scrollTop ?? 0)).toBeGreaterThan(0)
  const targetBox = await target.boundingBox()
  expect(targetBox).not.toBeNull()
  await page.mouse.move(targetBox!.x + targetBox!.width * .1, targetBox!.y + targetBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).toEqual([
    'vitamin_a_ug', 'protein_g', 'nutrition', 'calcium_mg', 'dietary_energy_kcal',
    'weight', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g',
  ])
  await page.mouse.up()
})

test('reversing across an overlapped displaced card restores the original order', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  await page.setViewportSize({ width: 1692, height: 904 })
  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'vitamin_a_ug')
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()

  const origin = { x: handleBox!.x + handleBox!.width / 2, y: handleBox!.y + handleBox!.height / 2 }
  await page.mouse.move(origin.x, origin.y)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(targetBox!.x + targetBox!.width * .1, targetBox!.y + targetBox!.height * .1, { steps: 12 })
  await expect.poll(() => cardOrder(page)).toEqual(metricBeforeBuiltInOrder)

  await page.mouse.move(origin.x, origin.y, { steps: 12 })
  await expect.poll(() => cardOrder(page)).toEqual(initialCards)
  await page.mouse.up()

  await expect.poll(() => persistedWrites).toEqual([])
  expect(state.activeCards).toEqual(initialCards)
})

test('one pointer move across several cards selects the last crossed slot', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  const initialOrder = ['weight', 'vitamin_a_ug', 'protein_g', 'dietary_energy_kcal']
  const expectedOrder = ['vitamin_a_ug', 'protein_g', 'dietary_energy_kcal', 'weight']
  state.activeCards = [...initialOrder]
  await page.setViewportSize({ width: 1692, height: 904 })
  await page.goto('/analyse?period=7')
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(initialOrder.length)
  await expect.poll(() => cardOrder(page)).toEqual(initialOrder)

  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'dietary_energy_kcal')
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(targetBox!.x + 5, targetBox!.y + 15)
  await expect.poll(() => cardOrder(page)).toEqual(expectedOrder)

  const draggedBox = await source.boundingBox()
  expect(draggedBox).not.toBeNull()
  const overlapWidth = Math.max(0, Math.min(draggedBox!.x + draggedBox!.width, targetBox!.x + targetBox!.width) - Math.max(draggedBox!.x, targetBox!.x))
  const overlapHeight = Math.max(0, Math.min(draggedBox!.y + draggedBox!.height, targetBox!.y + targetBox!.height) - Math.max(draggedBox!.y, targetBox!.y))
  expect(overlapWidth * overlapHeight / (targetBox!.width * targetBox!.height)).toBeLessThan(.5)

  await page.mouse.up()
  await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: expectedOrder }])
  expect(state.activeCards).toEqual(expectedOrder)
})

test('Cross-type metric pointer drag changes the mixed order and persists after reload', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  const originalOrder = [...initialCards]
  const { source } = await startDrag(page, 'vitamin_a_ug', 'weight')
  await expect.poll(() => cardOrder(page)).not.toEqual(originalOrder)
  const reorderedOrder = await cardOrder(page)
  expect(reorderedOrder.indexOf('vitamin_a_ug')).not.toBe(originalOrder.indexOf('vitamin_a_ug'))
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.up()
  await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: reorderedOrder }])
  expect(state.activeCards).toEqual(reorderedOrder)
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(reorderedOrder)
})
test('Summary-to-metric pointer drop persists the reordered mixed list', async ({ page }) => {
  const { persistedWrites, state } = await installSyntheticAnalysisApi(page)
  const initialOrder = ['weight', 'nutrition', 'vitamin_a_ug', 'micronutrients', 'activity', 'dietary_energy_kcal']
  let expectedOrder = initialOrder
  state.activeCards = [...initialOrder]
  await page.setViewportSize({ width: 1692, height: 904 })
  await page.goto('/analyse?period=30')
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(initialOrder.length)
  await expect.poll(() => cardOrder(page)).toEqual(initialOrder)

  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'vitamin_a_ug')
  const handleBox = await source.locator('.analysis-drag-handle').boundingBox()
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  const dropPoint = {
    x: targetBox!.x + targetBox!.width * .75,
    y: targetBox!.y + targetBox!.height / 2,
  }

  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
  await page.mouse.down()
  await expect(source).toHaveClass(/is-dragging/u)
  await page.mouse.move(dropPoint.x, dropPoint.y, { steps: 8 })
  await expect.poll(() => cardOrder(page)).not.toEqual(initialOrder)
  expectedOrder = await cardOrder(page)

  await page.mouse.up()

  await expect.poll(() => cardOrder(page)).toEqual(expectedOrder)
  await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: expectedOrder }])
  expect(state.activeCards).toEqual(expectedOrder)
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(expectedOrder)
})


test('touch cross-type reorder and keyboard moves persist one mixed list', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 390, height: 1200 })
  await page.goto('/analyse?period=7')
  const source = hubCard(page, 'weight')
  const target = hubCard(page, 'vitamin_a_ug')
  const handle = source.locator('.analysis-drag-handle')
  await expect(handle).toHaveCSS('touch-action', 'none')
  const handleBox = await handle.boundingBox()
  const targetBox = await target.boundingBox()
  expect(handleBox).not.toBeNull()
  expect(targetBox).not.toBeNull()
  const touch = await page.context().newCDPSession(page)
  await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: handleBox!.x + handleBox!.width / 2, y: handleBox!.y + handleBox!.height / 2, id: 1 }] })
  await expect(source).toHaveClass(/is-dragging/u)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: targetBox!.x + targetBox!.width / 2, y: targetBox!.y + targetBox!.height * .85, id: 1 }] })
  await expect.poll(() => cardOrder(page)).toEqual(metricBeforeBuiltInOrder)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect.poll(() => persistedWrites).toEqual([{ active_card_ids: metricBeforeBuiltInOrder }])
  await touch.detach()
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(metricBeforeBuiltInOrder)

  const weight = hubCard(page, 'weight')
  await weight.locator('.analysis-overflow-trigger').focus()
  await page.keyboard.press('Enter')
  const forward = weight.getByRole('button', { name: /Gewicht nach vorne verschieben/u })
  await expect(forward).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(forward).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(weight.locator('.analysis-overflow-trigger')).toBeFocused()
  await expect.poll(() => persistedWrites).toEqual([
    { active_card_ids: metricBeforeBuiltInOrder }, { active_card_ids: initialCards },
  ])
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(initialCards)

  const protein = hubCard(page, 'protein_g')
  await protein.locator('.analysis-overflow-trigger').focus()
  await page.keyboard.press('Enter')
  const backward = protein.getByRole('button', { name: /nach hinten verschieben/u })
  await backward.focus()
  await page.keyboard.press('Enter')
  await expect(protein.locator('.analysis-overflow-trigger')).toBeFocused()
  const keyboardOrder = ['weight', 'vitamin_a_ug', 'nutrition', 'protein_g', 'calcium_mg', 'dietary_energy_kcal', 'micronutrients', 'activity', 'fat_g', 'carbohydrates_g']
  await expect.poll(() => cardOrder(page)).toEqual(keyboardOrder)
  await expect.poll(() => persistedWrites).toEqual([
    { active_card_ids: metricBeforeBuiltInOrder }, { active_card_ids: initialCards }, { active_card_ids: keyboardOrder },
  ])
  await page.reload()
  await expect.poll(() => cardOrder(page)).toEqual(keyboardOrder)
})

test('Built-in and metric cards keep list-item identities in one mixed list', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await openHub(page)
  await expect(page.locator('.analysis-card-list > li[data-card-key]')).toHaveCount(initialCards.length)
  await expect(page.locator('.analysis-card-list [data-card-key="weight_kg"], .analysis-card-list [data-card-key="active_energy_kcal"]')).toHaveCount(0)
  const micro = hubCard(page, 'micronutrients')
  await expect(micro).toHaveAttribute('data-analysis-summary', 'micronutrients')
  await expect(micro).not.toHaveAttribute('role', /.+/u)
  await expect(micro.locator('.analysis-nutrient-count')).toContainText('2 von 2')
  await expect(micro.locator('.analysis-card')).toHaveCount(0)
  await expect(micro.locator('.analysis-overflow-trigger')).toHaveCount(1)
  await expect(hubCard(page, 'vitamin_a_ug')).toHaveAttribute('data-analysis-key', 'vitamin_a_ug')
  await expect(hubCard(page, 'vitamin_a_ug').locator('.analysis-overflow-trigger')).toHaveCount(1)
})
test('removing a Hub card focuses the adjacent surviving action', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  const weight = hubCard(page, 'weight')
  await weight.locator('.analysis-overflow-trigger').click()
  const remove = weight.locator('.analysis-overflow-menu [data-card-remove]')
  await expect(remove).toBeVisible()
  await remove.click()
  await expect(weight).toHaveCount(0)
  await expect(hubCard(page, 'vitamin_a_ug').locator('.analysis-overflow-trigger')).toBeFocused()
  await expect.poll(() => persistedWrites).toEqual([{
    active_card_ids: initialCards.filter((key) => key !== 'weight'),
  }])
})

test('overflow menus are mutually exclusive and toggling the current trigger closes it', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await openHub(page)
  const weight = hubCard(page, 'weight').locator('.analysis-overflow-trigger')
  const activity = hubCard(page, 'activity').locator('.analysis-overflow-trigger')
  await weight.click()
  await expect(page.locator('.analysis-overflow-menu:visible')).toHaveCount(1)
  await activity.click()
  await expect(hubCard(page, 'weight').locator('.analysis-overflow-menu')).not.toBeVisible()
  await expect(hubCard(page, 'activity').locator('.analysis-overflow-menu')).toBeVisible()
  await expect(page.locator('.analysis-overflow-menu:visible')).toHaveCount(1)
  await activity.click()
  await expect(page.locator('.analysis-overflow-menu:visible')).toHaveCount(0)
})

for (const closeBy of ['outside', 'Escape', 'navigation', 'drag'] as const) {
  test(`overflow closes on ${closeBy} without a preference write`, async ({ page }) => {
    const { persistedWrites } = await installSyntheticAnalysisApi(page)
    await openHub(page)
    const weight = hubCard(page, 'weight')
    await weight.locator('.analysis-overflow-trigger').click()
    await expect(weight.locator('.analysis-overflow-menu')).toBeVisible()
    if (closeBy === 'outside') await page.getByRole('heading', { name: 'Meine Analysen', exact: true }).click()
    else if (closeBy === 'Escape') {
      const moveAction = weight.getByRole('button', { name: /nach hinten verschieben/u })
      await moveAction.focus()
      await expect(moveAction).toBeFocused()
      await page.keyboard.press('Escape')
      await expect(weight.locator('.analysis-overflow-trigger')).toBeFocused()
    }
    else if (closeBy === 'navigation') {
      await hubCard(page, 'activity').locator('.analysis-card-heading a').click()
      await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/aktivitaet/aktive-energie')
      await page.goBack()
      await expect(weight).toBeVisible()
    } else {
      const handleBox = await weight.locator('.analysis-drag-handle').boundingBox()
      await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2)
      await page.mouse.down()
      await expect(weight).toHaveClass(/is-dragging/u)
      await expect(page.locator('.analysis-overflow-menu:visible')).toHaveCount(0)
      await page.mouse.up()
    }
    await expect(page.locator('.analysis-overflow-menu:visible')).toHaveCount(0)
    expect(persistedWrites).toEqual([])
  })
}

test('hub remains touch-scrollable outside the drag handle', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/analyse?period=7')
  const content = hubCard(page, 'weight').locator('.analysis-card-content')
  await expect(content).toBeVisible()
  const box = await content.boundingBox()
  const touch = await page.context().newCDPSession(page)
  await touch.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 })
  await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box!.x + box!.width / 2, y: box!.y + box!.height / 2, id: 1 }] })
  await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 - 160, id: 1 }] })
  await page.waitForTimeout(120)
  await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  await expect(page.locator('.analysis-card.is-dragging')).toHaveCount(0)
  expect(persistedWrites).toEqual([])
  await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
  await expect.poll(() => cardOrder(page)).toEqual(initialCards)
  await touch.detach()
})

test('reduced motion removes lift animation without disabling mixed-list drag', async ({ page }) => {
  await installSyntheticAnalysisApi(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await openHub(page)
  const card = hubCard(page, 'weight')
  const handle = card.locator('.analysis-drag-handle')
  const box = await handle.boundingBox()
  const x = box!.x + box!.width / 2
  const y = box!.y + box!.height / 2
  await handle.dispatchEvent('pointerdown', { bubbles: true, cancelable: true, pointerId: 58, pointerType: 'mouse', isPrimary: true, button: 0, clientX: x, clientY: y })
  await expect(card).toHaveClass(/is-dragging/u)
  await dispatchPointer(page, 'pointermove', 58, x + 20, y + 24)
  const feedback = await card.evaluate((element) => ({ translate: getComputedStyle(element).translate, scale: getComputedStyle(element).scale, duration: getComputedStyle(element).transitionDuration }))
  expect(feedback.translate).not.toBe('none')
  expect(feedback.scale).toBe('1')
  expect(feedback.duration.split(',').every((duration) => Number.parseFloat(duration) === 0)).toBe(true)
  await dispatchPointer(page, 'pointercancel', 58, x, y)
})

test('Mixed cards keep compact chrome, accessible handles and separate navigation links', async ({ page }) => {
  const { persistedWrites } = await installSyntheticAnalysisApi(page)
  await openHub(page)
  for (const width of [1568, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    const firstRowCards = page.locator('.analysis-card-list > li:nth-child(-n + 4)')
    await expect(firstRowCards).toHaveCount(4)
    const boxes = await firstRowCards.evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().top))
    expect(new Set(boxes.map(Math.round)).size).toBe(1)
    for (const key of initialCards.filter((cardKey) => cardKey !== 'micronutrients')) {
      const header = hubCard(page, key).locator('.analysis-card-header')
      const geometry = await header.evaluate((element) => {
        const rect = (selector: string) => {
          const box = element.querySelector<HTMLElement>(selector)!.getBoundingClientRect()
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height }
        }
        return {
          icon: rect('.analysis-card-icon'),
          heading: rect('.analysis-card-heading'),
          handle: rect('.analysis-drag-handle'),
          overflow: rect('.analysis-overflow-trigger'),
          header: element.getBoundingClientRect(),
        }
      })
      expect(geometry.icon.right).toBeLessThanOrEqual(geometry.heading.left + 1)
      expect(geometry.heading.right).toBeLessThanOrEqual(geometry.handle.left + 1)
      expect(geometry.handle.right).toBeLessThanOrEqual(geometry.overflow.left + 1)
      expect(geometry.overflow.right).toBeLessThanOrEqual(geometry.header.right + 1)
      expect(geometry.handle.width).toBeGreaterThanOrEqual(43.99)
      expect(geometry.handle.height).toBeGreaterThanOrEqual(43.99)
      expect(geometry.overflow.width).toBeGreaterThanOrEqual(43.99)
      expect(geometry.overflow.height).toBeGreaterThanOrEqual(43.99)
    }
  }
  const weight = hubCard(page, 'weight')
  const handle = weight.locator('.analysis-drag-handle')
  await expect(handle).toHaveAccessibleName(/Gewicht/u)
  await expect(weight.locator('.analysis-overflow-trigger')).toHaveAccessibleName(/Gewicht/u)
  await handle.evaluate((element) => (element as HTMLButtonElement).click())
  await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
  expect(persistedWrites).toEqual([])
  await weight.locator('.analysis-card-heading a').click()
  await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/koerper/gewicht')
  await expect(page.locator('.weight-summary-primary')).toContainText('69,5 kg')
  await page.goto('/analyse?period=7')
  const activity = hubCard(page, 'activity')
  await expect(activity.locator('.analysis-metric-value')).toHaveText('220 kcal')
  await activity.locator('.analysis-card-heading a').click()
  await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/aktivitaet/aktive-energie')
  await expect(page.locator('.activity-reading-list')).toContainText('220 kcal')
})


for (const outcome of ['cancel', 'outside', 'reject'] as const) {
  test(`Built-in card pointer ${outcome} preserves the mixed order without detail click-through`, async ({ page }) => {
    const { persistedWrites } = await installSyntheticAnalysisApi(page, outcome === 'reject')
    await openHub(page)
    await startDrag(page, 'weight', 'vitamin_a_ug')
    await expect.poll(() => cardOrder(page)).not.toEqual(initialCards)
    const attemptedOrder = await cardOrder(page)
    if (outcome === 'cancel') await dispatchPointer(page, 'pointercancel', 1, 0, 0)
    else if (outcome === 'outside') await page.mouse.move(2, 2)
    await page.mouse.up()
    await expect.poll(() => cardOrder(page)).toEqual(initialCards)
    expect(persistedWrites).toEqual(outcome === 'reject'
      ? [{ active_card_ids: attemptedOrder }]
      : [])
    await expect(page.locator('[data-drop-state="dropped"], .analysis-card.is-settled')).toHaveCount(0)
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
    if (outcome === 'reject') await expect(page.locator('.analysis-save-error')).toBeVisible()
    await page.reload()
    await expect.poll(() => cardOrder(page)).toEqual(initialCards)
  })
}
