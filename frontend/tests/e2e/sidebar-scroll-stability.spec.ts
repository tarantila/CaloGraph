import { expect, test } from '@playwright/test'

const weekdays = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag']

test('sidebar geometry stays stationary at the bottom of a long page', async ({ page }) => {
  await page.setViewportSize({ width: 1748, height: 985 })
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/auth/csrf')) {
      return route.fulfill({ json: { csrf_token: 'synthetic-csrf-token' } })
    }
    if (path.endsWith('/auth/me')) {
      return route.fulfill({
        json: {
          id: 'sidebar-layout-review',
          username: 'sidebar-layout-review',
          language: 'de',
          timezone: 'Europe/Berlin',
          week_starts_on: 0,
          raw_payload_retention_days: 0,
          is_admin: false,
        },
      })
    }
    if (path.endsWith('/settings/onboarding')) {
      return route.fulfill({
        json: { mode: 'legacy', required: false, completed: true, current_step: 'completed' },
      })
    }
    if (path.endsWith('/achievements/reconcile')) {
      return route.fulfill({ json: { achievements: [], newly_unlocked: [] } })
    }
    if (path.endsWith('/analytics/weekdays')) {
      return route.fulfill({
        json: {
          weekdays: weekdays.map((label, weekday) => ({
            weekday,
            label,
            count: 1,
            mean_kcal: 1800,
            median_kcal: 1780,
            p25_kcal: 1650,
            p75_kcal: 1950,
            mean_deviation_kcal: -200,
            mean_protein_g: 120,
          })),
        },
      })
    }
    return route.fulfill({ status: 404, json: { detail: `Unhandled review route: ${path}` } })
  })

  await page.goto('/wochentage?period=30')
  await expect(page.getByRole('heading', { name: 'Wochentagsanalyse' })).toBeVisible()
  await expect(page.locator('table tbody tr')).toHaveCount(7)

  const readSidebarGeometry = () =>
    page.locator('.sidebar').evaluate((sidebar) => {
      const rect = (element: Element | null) => {
        if (!element) throw new Error('Expected shared sidebar element')
        const bounds = element.getBoundingClientRect()
        return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }
      }
      const links = [...sidebar.querySelectorAll<HTMLAnchorElement>('.sidebar-primary-navigation a')]
      const first = links[0]
      const active = links.find((link) => link.classList.contains('active'))
      const last = links.at(-1)
      if (!first || !active || !last) throw new Error('Expected complete primary navigation')
      return {
        sidebar: rect(sidebar),
        brand: rect(sidebar.querySelector('.brand')),
        brandText: rect(sidebar.querySelector('.brand strong')),
        logo: rect(sidebar.querySelector('.brand-logo')),
        first: rect(first),
        firstIcon: rect(first.querySelector('svg')),
        active: rect(active),
        activeIcon: rect(active.querySelector('svg')),
        last: rect(last),
        lastIcon: rect(last.querySelector('svg')),
      }
    })

  await page.evaluate(async () => {
    await document.fonts.ready
  })
  await page.evaluate(() => window.scrollTo(0, 0))
  const topGeometry = await readSidebarGeometry()
  const scrollRange = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)
  expect(scrollRange).toBeGreaterThan(0)

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  expect(await page.evaluate(() => window.scrollY)).toBe(scrollRange)
  expect(await readSidebarGeometry()).toEqual(topGeometry)
})
