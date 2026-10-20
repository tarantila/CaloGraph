import { expect, test } from '@playwright/test'

const events = [
  { event_id: '00000000-0000-4000-8000-000000000001', detail_available: true, provider_key: 'yazio', local_time: '08:10', meal_type: 'breakfast', display_name: 'Schwarzer Kaffee', calories_kcal: 10, protein_g: 1, carbohydrates_g: 1.5, fat_g: 0, serving_amount: 500, serving_unit: 'ml' },
  { event_id: '00000000-0000-4000-8000-000000000002', detail_available: true, provider_key: 'yazio', local_time: '08:15', meal_type: 'breakfast', display_name: 'Haferdrink', calories_kcal: 20, protein_g: 1, carbohydrates_g: 3, fat_g: 1, serving_amount: 100, serving_unit: 'ml' },
  { event_id: '00000000-0000-4000-8000-000000000003', detail_available: true, provider_key: 'yazio', local_time: '08:20', meal_type: 'breakfast', display_name: 'Brot', calories_kcal: 35, protein_g: 2, carbohydrates_g: 6, fat_g: 1, serving_amount: 1, serving_unit: 'portion' },
  { event_id: '00000000-0000-4000-8000-000000000004', detail_available: true, provider_key: 'yazio', local_time: '12:30', meal_type: 'lunch', display_name: 'Corny Protein Erdnuss', calories_kcal: 86, protein_g: 8, carbohydrates_g: 10, fat_g: 2, serving_amount: 1, serving_unit: 'portion' },
  { event_id: '00000000-0000-4000-8000-000000000005', detail_available: true, provider_key: 'yazio', local_time: '19:30', meal_type: 'dinner', display_name: 'High Protein Penne', calories_kcal: 993, protein_g: 40, carbohydrates_g: 120, fat_g: 55, serving_amount: 1, serving_unit: 'portion' },
  { event_id: '00000000-0000-4000-8000-000000000006', detail_available: true, provider_key: 'yazio', local_time: '21:00', meal_type: 'snack', display_name: 'Proteinriegel', calories_kcal: 150, protein_g: 5, carbohydrates_g: 20, fat_g: 4, serving_amount: 1, serving_unit: 'portion' },
  { event_id: '00000000-0000-4000-8000-000000000007', detail_available: true, provider_key: 'yazio', local_time: null, meal_type: 'provider-dessert', display_name: 'Dessert', calories_kcal: 30, protein_g: 1, carbohydrates_g: 4, fat_g: 1, serving_amount: 1, serving_unit: 'portion' },
]

const group = {
  provider_key: 'yazio',
  status: 'available',
  record_count: events.length,
  summary: { calories_kcal: 1324, protein_g: 58, carbohydrates_g: 164.5, fat_g: 64 },
  events,
}

test('renders meal occasion summaries without overflow at desktop, tablet, and mobile widths', async ({ page }) => {
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
          id: 'nutrition-meal-review',
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
    if (path.endsWith('/settings/onboarding')) {
      return route.fulfill({ json: { mode: 'legacy', required: false, completed: true, current_step: 'completed' } })
    }
    if (path.endsWith('/settings/provider-preferences')) {
      return route.fulfill({ json: { preferences: [] } })
    }
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'nutrition-review-csrf' } })
    }
    if (path.endsWith('/achievements/reconcile')) {
      return route.fulfill({ json: { achievements: [], newly_unlocked: [] } })
    }
    if (path.endsWith('/analytics/verification/nutrition')) {
      const requestUrl = new URL(route.request().url())
      return route.fulfill({
        json: {
          date: requestUrl.searchParams.get('date') ?? '2026-10-16',
          view: 'canonical',
          canonical: group,
          providers: [group],
        },
      })
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/ernaehrung')
  await expect(page.getByRole('heading', { name: 'Ernährung' })).toBeVisible()
  const groups = page.locator('tbody.verification-meal-occasion')
  await expect(groups).toHaveCount(5)
  await expect(groups.nth(0).locator('.verification-meal-occasion-label')).toHaveText('Frühstück')
  await expect(groups.nth(0).locator('.verification-meal-occasion-entry-count')).toHaveText('3 Einträge')
  await expect(groups.nth(0).locator('.verification-meal-occasion-total')).toHaveText('65 kcal')
  await expect(groups.nth(1).locator('.verification-meal-occasion-label')).toHaveText('Mittagessen')
  await expect(groups.nth(2).locator('.verification-meal-occasion-total')).toHaveText('993 kcal')
  await expect(groups.nth(1).locator('.verification-meal-occasion-total')).toHaveText('86 kcal')
  await expect(groups.nth(4).locator('.verification-meal-occasion-label')).toHaveText('Ohne Kategorie')
  await expect(groups.first().locator('.verification-meal-occasion-icon')).toHaveAttribute('aria-hidden', 'true')
  await expect(page.locator('.verification-meal-subtotal')).toHaveCount(0)
  for (const theme of ['light', 'dark']) {
    await page.locator('html').evaluate((element, value) => element.setAttribute('data-theme', value), theme)
    const colorAudit = await groups.evaluateAll((occasions) => {
      const rgb = (color: string): number[] => (color.match(/[\d.]+/gu) ?? []).slice(0, 3).map(Number)
      const luminance = (color: string): number => {
        const channels = rgb(color).map((channel) => {
          const normalized = color.startsWith('color(srgb') ? channel : channel / 255
          return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
        })
        return 0.2126 * channels[0]! + 0.7152 * channels[1]! + 0.0722 * channels[2]!
      }
      const contrast = (foreground: string, background: string): number => {
        const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
        return (values[0]! + 0.05) / (values[1]! + 0.05)
      }
      return occasions.map((occasion) => {
        const header = occasion.querySelector('.verification-meal-occasion-title')!
        const label = occasion.querySelector('.verification-meal-occasion-label')!
        const count = occasion.querySelector('.verification-meal-occasion-entry-count')!
        const total = occasion.querySelector('.verification-meal-occasion-total')!
        const headerBackground = getComputedStyle(header).backgroundColor
        const totalBackground = getComputedStyle(total).backgroundColor
        return {
          headerBackground,
          labelContrast: contrast(getComputedStyle(label).color, headerBackground),
          countContrast: contrast(getComputedStyle(count).color, headerBackground),
          totalContrast: contrast(getComputedStyle(total).color, totalBackground),
        }
      })
    })
    expect(new Set(colorAudit.map((color) => color.headerBackground)).size, `${theme} meal accents`).toBe(5)
    expect(colorAudit.every((color) =>
      color.labelContrast >= 4.5 && color.countContrast >= 4.5 && color.totalContrast >= 4.5,
    ), `${theme} meal header text contrast: ${JSON.stringify(colorAudit)}`).toBe(true)
  }


  for (const width of [1440, 1280, 1024, 768, 390]) {
    await page.setViewportSize({ width, height: 900 })
    const layout = await page.evaluate(() => {
      const tableScroller = document.querySelector('.nutrition-food-table-scroll')
      const groupLayouts = Array.from(document.querySelectorAll('tbody.verification-meal-occasion')).map((occasion) => {
        const header = occasion.querySelector('.verification-meal-occasion-title')
        const identity = occasion.querySelector('.verification-meal-occasion-identity')
        const total = occasion.querySelector('.verification-meal-occasion-total')
        if (!(header instanceof HTMLElement) || !(identity instanceof HTMLElement) || !(total instanceof HTMLElement)) {
          return null
        }
        const headerRect = header.getBoundingClientRect()
        const identityRect = identity.getBoundingClientRect()
        const totalRect = total.getBoundingClientRect()
        return {
          contained: identityRect.left >= headerRect.left - 1
            && identityRect.right <= headerRect.right + 1
            && totalRect.left >= headerRect.left - 1
            && totalRect.right <= headerRect.right + 1,
          overlap: identityRect.left < totalRect.right
            && identityRect.right > totalRect.left
            && identityRect.top < totalRect.bottom
            && identityRect.bottom > totalRect.top,
        }
      })
      const firstLabel = document.querySelector('.verification-meal-occasion-label')
      return {
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
        tableClientWidth: tableScroller?.clientWidth ?? 0,
        tableScrollWidth: tableScroller?.scrollWidth ?? 0,
        groupLayouts,
        textTransform: firstLabel ? getComputedStyle(firstLabel).textTransform : null,
      }
    })

    expect(layout.documentWidth, `page overflow at ${width}px`).toBeLessThanOrEqual(layout.viewportWidth)
    expect(layout.tableClientWidth, `table missing at ${width}px`).toBeGreaterThan(0)
    expect(layout.tableScrollWidth, `table overflow at ${width}px`).toBeLessThanOrEqual(layout.tableClientWidth + 1)
    expect(layout.groupLayouts, `summary missing at ${width}px`).not.toContain(null)
    expect(layout.textTransform, `meal title not in title case at ${width}px`).toBe('none')
    for (const occasion of layout.groupLayouts) {
      expect(occasion?.contained, `summary escapes rowgroup at ${width}px`).toBe(true)
      expect(occasion?.overlap, `title overlaps kcal at ${width}px`).toBe(false)
    }
  }

  expect(browserErrors).toEqual([])
})
