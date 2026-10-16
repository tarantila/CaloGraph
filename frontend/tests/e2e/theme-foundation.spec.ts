import { expect, test } from '@playwright/test'

function contrastRatio(foreground: string, background: string): number {
  const luminance = (color: string) => {
    const srgb = color.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*[\d.]+)?\)$/)
    const channels = srgb
      ? srgb.slice(1, 4).map((channel) => Number(channel) * 255)
      : color.match(/[\d.]+/g)?.slice(0, 3).map(Number)
    if (!channels || channels.length !== 3) throw new Error(`Unsupported computed color: ${color}`)
    const linear = channels.map((channel) => {
      const normalized = channel / 255
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
    })
    return linear[0]! * 0.2126 + linear[1]! * 0.7152 + linear[2]! * 0.0722
  }
  const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (values[0]! + 0.05) / (values[1]! + 0.05)
}

test('shared palette provides readable light and dark surfaces', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('afterbegin', `
      <label class="field"><span>Field</span><input id="theme-input" /></label>
    `)
    document.body.insertAdjacentHTML('beforeend', `
      <aside class="sidebar"><nav><a class="active" href="#">Selected</a></nav></aside>
      <section class="card" id="theme-card">Readable surface</section>
      <button class="button" id="theme-button" type="button">Action</button>
      <section class="table-card"><table><tbody><tr><td>Row</td></tr></tbody></table></section>
      <div class="analysis-overflow-menu" id="theme-popover">Menu</div>
    `)
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  })
  await page.keyboard.press('Tab')
  await expect(page.locator('#theme-input')).toBeFocused()

  const readTheme = async (theme: 'dark' | 'light') => {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value
    }, theme)
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())))
    return await page.evaluate(() => {
      const root = getComputedStyle(document.documentElement)
      const card = getComputedStyle(document.querySelector<HTMLElement>('#theme-card')!)
      const button = getComputedStyle(document.querySelector<HTMLButtonElement>('#theme-button')!)
      const activeLink = document.querySelector<HTMLElement>('.sidebar nav a.active')!
      const input = getComputedStyle(document.querySelector<HTMLInputElement>('#theme-input')!)
      const popover = getComputedStyle(document.querySelector<HTMLElement>('#theme-popover')!)
      return {
        page: root.backgroundColor,
        scheme: root.colorScheme,
        card: card.backgroundColor,
        cardInk: card.color,
        buttonInk: button.color,
        buttonBackground: button.backgroundColor,
        activeBackground: getComputedStyle(activeLink).backgroundColor,
        activeClass: activeLink.classList.contains('active'),
        focusStyle: input.outlineStyle,
        focusColor: input.outlineColor,
        popoverBackground: popover.backgroundColor,
      }
    })
  }

  const dark = await readTheme('dark')
  const light = await readTheme('light')

  expect(dark.scheme).toContain('dark')
  expect(light.scheme).toBe('light')
  expect(light.page).not.toBe(dark.page)
  expect(light.card).not.toBe(dark.card)
  expect(contrastRatio(light.cardInk, light.card)).toBeGreaterThanOrEqual(4.5)
  expect(contrastRatio(light.buttonInk, light.buttonBackground)).toBeGreaterThanOrEqual(4.5)
  expect(light.activeClass).toBe(true)
  expect(light.activeBackground).not.toBe('rgba(0, 0, 0, 0)')
  expect(light.focusStyle).not.toBe('none')
  expect(light.focusColor).not.toBe('rgba(0, 0, 0, 0)')
  expect(light.popoverBackground).not.toBe('rgba(0, 0, 0, 0)')
})
test('uses the prescribed light chart palette and keeps dark roles unchanged', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => {
    const seedKey = 'calograph.theme-foundation-seeded'
    if (sessionStorage.getItem(seedKey) !== null) return
    if (localStorage.getItem('calograph.theme') === null) localStorage.setItem('calograph.theme', 'light')
    sessionStorage.setItem(seedKey, '1')
  })
  await page.route('**/api/**', (route) => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Synthetic unauthenticated session' }),
  }))
  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')

  const readSeriesColors = () => page.evaluate(() => {
    const style = getComputedStyle(document.documentElement)
    const color = (name: string) => style.getPropertyValue(name).trim()
    return {
      violet: color('--chart-series-violet'),
      teal: color('--chart-series-teal'),
      blue: color('--chart-series-blue'),
      amber: color('--chart-series-amber'),
      budget: color('--chart-series-budget'),
      baseline: color('--chart-series-baseline'),
      lilac: color('--chart-series-lilac'),
    }
  })
  const light = {
    violet: '#7c3aed',
    teal: '#278979',
    blue: '#2563eb',
    amber: '#d97706',
    budget: '#d97706',
    baseline: '#64748b',
    lilac: '#a855f7',
  }
  const readTodayAccent = () => page.evaluate(() => {
    const swatch = document.createElement('div')
    swatch.className = 'metric-card-label violet'
    document.body.append(swatch)
    const color = getComputedStyle(swatch).color
    swatch.remove()
    return color
  })
  expect(await readSeriesColors()).toEqual(light)
  expect(await readTodayAccent()).toBe('rgb(124, 58, 237)')

  await page.evaluate(() => localStorage.setItem('calograph.theme', 'dark'))
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await readSeriesColors()).toEqual({
    violet: '#8b5cf6',
    teal: '#2dd4bf',
    blue: '#5b8ff9',
    amber: '#f6c445',
    budget: '#fb923c',
    baseline: '#64748b',
    lilac: '#d19bff',
  })
  expect(await readTodayAccent()).toBe('rgb(139, 92, 246)')

  await page.evaluate(() => localStorage.removeItem('calograph.theme'))
  await page.emulateMedia({ colorScheme: 'light' })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme-preference', 'system')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await readSeriesColors()).toEqual(light)
  expect(await readTodayAccent()).toBe('rgb(124, 58, 237)')

  const fallback = await page.evaluate(() => {
    document.documentElement.removeAttribute('data-theme')
    const style = getComputedStyle(document.documentElement)
    const color = (name: string) => style.getPropertyValue(name).trim()
    return {
      violet: color('--chart-series-violet'),
      teal: color('--chart-series-teal'),
      blue: color('--chart-series-blue'),
      amber: color('--chart-series-amber'),
      budget: color('--chart-series-budget'),
      baseline: color('--chart-series-baseline'),
      lilac: color('--chart-series-lilac'),
    }
  })
  expect(fallback).toEqual(light)
  expect(await readTodayAccent()).toBe('rgb(124, 58, 237)')
})

test('shared controls, status surfaces, and calendar follow the selected theme', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('beforeend', `
      <label class="field">Provider<select id="theme-select"><option>Local</option></select></label>
      <div class="date-input" id="theme-date"><input aria-label="Date" value="2026-01-01" /></div>
      <button class="button" id="theme-selected-button" type="button" aria-pressed="true">Selected action</button>
      <div class="verification-date-navigation" id="theme-verification"><strong>January 2026</strong></div>
      <button class="button" id="theme-disabled" type="button" disabled>Unavailable</button>
      <section class="card"><p class="error" id="theme-error">Unavailable state</p></section>
      <span class="badge complete" id="theme-status">Complete</span>
      <article class="calendar-day under" id="theme-calendar">
        <div class="calendar-day-heading"><strong>Mon</strong><time>01 Jan</time></div>
        <b>1,800 <small>kcal</small></b>
        <span>Within budget</span>
      </article>
      <section class="card action-dialog" id="theme-dialog"><h2>Confirm</h2><p>Dialog content</p></section>
      <span id="theme-ink-swatch" style="color:var(--ink)">Ink</span>
      <span id="theme-danger-swatch" style="color:var(--danger)">Danger</span>
      <span id="theme-control-swatch" style="background:var(--control-bg)">Control</span>
      <span id="theme-surface-swatch" style="background:var(--surface)">Surface</span>
    `)
  })

  await page.evaluate(() => {
    document.documentElement.dataset.theme = 'light'
  })
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())))
  const state = await page.evaluate(() => {
    const style = (selector: string) => getComputedStyle(document.querySelector<HTMLElement>(selector)!)
    return {
      controlBackground: style('#theme-control-swatch').backgroundColor,
      surfaceBackground: style('#theme-surface-swatch').backgroundColor,
      selectBackground: style('#theme-select').backgroundColor,
      dateBackground: style('#theme-date').backgroundColor,
      verificationBackground: style('#theme-verification').backgroundColor,
      disabledOpacity: style('#theme-disabled').opacity,
      disabledColor: style('#theme-disabled').color,
      disabledBackground: style('#theme-disabled').backgroundColor,
      errorColor: style('#theme-error').color,
      expectedDanger: style('#theme-danger-swatch').color,
      statusColor: style('#theme-status').color,
      statusBackground: style('#theme-status').backgroundColor,
      calendarBackground: style('#theme-calendar').backgroundColor,
      calendarTextColor: style('#theme-calendar b').color,
      expectedInk: style('#theme-ink-swatch').color,
      dialogHeadingColor: style('#theme-dialog h2').color,
      selectedBackground: style('#theme-selected-button').backgroundColor,
      selectedInk: style('#theme-selected-button').color,
    }
  })

  expect(state.selectBackground).toBe(state.controlBackground)
  expect(state.dateBackground).toBe(state.controlBackground)
  expect(state.verificationBackground).toBe(state.controlBackground)
  expect(state.selectedBackground).not.toBe('rgba(0, 0, 0, 0)')
  expect(contrastRatio(state.selectedInk, state.selectedBackground)).toBeGreaterThanOrEqual(4.5)
  expect(state.disabledOpacity).toBe('1')
  expect(contrastRatio(state.disabledColor, state.disabledBackground)).toBeGreaterThanOrEqual(4.5)
  expect(state.errorColor).toBe(state.expectedDanger)
  expect(contrastRatio(state.statusColor, state.statusBackground)).toBeGreaterThanOrEqual(4.5)
  expect(state.calendarBackground).toBe(state.surfaceBackground)
  expect(state.calendarTextColor).toBe(state.expectedInk)
  expect(state.dialogHeadingColor).toBe(state.expectedInk)
})
test('analysis card and overflow controls use semantic theme ink', async ({ page }) => {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\/v1/, '')
    const responses: Record<string, unknown> = {
      '/auth/me': {
        id: 'theme-test-user',
        username: 'theme-test',
        language: 'de',
        timezone: 'Europe/Berlin',
        week_starts_on: 1,
        preferred_weight_unit: 'kg',
        raw_payload_retention_days: 30,
        is_admin: false,
        is_active: true,
        deactivated_at: null,
      },
      '/settings/onboarding': {
        mode: 'legacy',
        required: false,
        completed: true,
        current_step: 'completed',
      },
      '/achievements/reconcile': { achievements: [], newly_unlocked: [] },
      '/settings/analysis-preferences': { active_card_ids: ['weight'] },
      '/analytics/registry': {
        analyses: [{
          analysis_key: 'weight_kg',
          category_key: 'body',
          domain_key: 'weight',
          status: 'available',
          card_capable: true,
          detail_capable: true,
          reader_key: 'weight',
          period_capability: { min_days: 7, max_days: 3660, custom_range: true },
        }],
      },
      '/analytics/weight': {
        start_date: '2026-01-01',
        end_date: '2026-12-31',
        selected_provider: null,
        points: [{ date: '2026-01-01', weight_kg: 42 }],
        summary: {
          measurement_count: 1,
          latest: { date: '2026-01-01', weight_kg: 42 },
          change_kg: 0,
          average_kg: 42,
          minimum_kg: 42,
          maximum_kg: 42,
        },
      },
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(responses[path] ?? {}),
    })
  })

  await page.goto('/analyse')
  await expect(page.locator('.analysis-metric-value').first()).toHaveText('42 kg')
  await page.locator('.analysis-overflow-trigger').first().click()
  await page.evaluate(() => {
    document.documentElement.style.setProperty('--text', '#ff00ff')
    document.documentElement.dataset.theme = 'light'
  })
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())))

  const state = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement)
    const card = document.querySelector<HTMLElement>('.analysis-card')!
    const kpi = document.querySelector<HTMLElement>('.analysis-metric-value')!
    const dragHandle = document.querySelector<HTMLButtonElement>('.analysis-drag-handle')!
    const popover = document.querySelector<HTMLElement>('.analysis-overflow-menu')!
    const menuButton = popover.querySelector<HTMLButtonElement>('button:not(:disabled)')!
    const disabledMenuButton = popover.querySelector<HTMLButtonElement>('button:disabled')!
    const ink = document.createElement('span')
    ink.style.color = 'var(--ink)'
    document.body.append(ink)
    const expectedInk = getComputedStyle(ink).color
    ink.style.color = 'var(--muted)'
    const expectedMuted = getComputedStyle(ink).color
    ink.remove()
    return {
      cardBackground: getComputedStyle(card).backgroundColor,
      kpiColor: getComputedStyle(kpi).color,
      dragColor: getComputedStyle(dragHandle).color,
      popoverBackground: getComputedStyle(popover).backgroundColor,
      expectedInk,
      expectedMuted,
      menuColor: getComputedStyle(menuButton).color,
      disabledMenuColor: getComputedStyle(disabledMenuButton).color,
      scheme: root.colorScheme,
    }
  })

  expect(state.scheme).toBe('light')
  expect(state.kpiColor).toBe(state.expectedInk)
  expect(state.dragColor).toBe(state.expectedMuted)
  expect(state.menuColor).toBe(state.expectedInk)
  expect(contrastRatio(state.kpiColor, state.cardBackground)).toBeGreaterThanOrEqual(4.5)
  expect(contrastRatio(state.dragColor, state.cardBackground)).toBeGreaterThanOrEqual(4.5)
  expect(contrastRatio(state.disabledMenuColor, state.popoverBackground)).toBeGreaterThanOrEqual(4.5)
})

test('keeps the local appearance selector usable across delayed and failed profile reads on mobile', async ({ page }) => {
  let profileReads = 0
  let profileWrites = 0
  const profile = {
    id: 'theme-test-user',
    username: 'theme-test',
    language: 'de',
    timezone: 'Europe/Berlin',
    week_starts_on: 0,
    preferred_weight_unit: 'kg',
    raw_payload_retention_days: 30,
    highlight_over_budget: false,
    is_admin: false,
    is_active: true,
    deactivated_at: null,
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace(/^\/api\/v1/u, '')
    if (path === '/auth/me') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(profile) })
      return
    }
    if (path === '/settings/onboarding') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ mode: 'legacy', required: false, completed: true, current_step: 'completed' }),
      })
      return
    }
    if (path === '/achievements/reconcile') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ achievements: [], newly_unlocked: [] }) })
      return
    }
    if (path === '/settings/profile' && request.method() === 'PUT') {
      profileWrites += 1
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(profile) })
      return
    }
    if (path === '/settings/profile') {
      profileReads += 1
      await new Promise((resolve) => setTimeout(resolve, 400))
      await route.fulfill({
        status: profileReads === 1 ? 200 : 503,
        contentType: 'application/json',
        body: JSON.stringify(profileReads === 1 ? profile : { detail: 'Synthetic profile read failure' }),
      })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })

  await page.goto('/konto/allgemeine-einstellungen')
  const appearance = page.locator('select[name="theme_preference"]')
  await expect(appearance).toBeVisible()
  await expect(appearance).toBeEnabled()
  expect(await appearance.evaluate((select) => select.closest('form') === null
    && select.closest('.account-appearance-card') !== null)).toBe(true)
  await expect(page.getByRole('status')).toContainText('Allgemeine Einstellungen werden geladen')

  await appearance.selectOption('light')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => localStorage.getItem('calograph.theme'))).toBe('light')
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(profileWrites).toBe(0)
  await expect(page.locator('select[name="language"]')).toBeVisible()
  const accountStack = page.locator('.account-page-stack')
  await expect(accountStack).toHaveCount(1)
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 })
    const layout = await accountStack.evaluate((element) => ({
      rowGap: getComputedStyle(element).rowGap,
      cardOrder: Array.from(element.children).map((child) => ({
        settings: child.classList.contains('account-form-card'),
        appearance: child.classList.contains('account-appearance-card'),
      })),
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    }))
    expect(layout.rowGap).toBe('16px')
    expect(layout.cardOrder).toEqual([
      { settings: true, appearance: false },
      { settings: false, appearance: true },
    ])
    expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth)
  }
  await page.setViewportSize({ width: 390, height: 844 })

  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Allgemeine Einstellungen konnten nicht geladen werden.')
  await expect(appearance).toHaveValue('light')
  await appearance.selectOption('dark')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await page.evaluate(() => localStorage.getItem('calograph.theme'))).toBe('dark')
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(profileReads).toBe(2)
  expect(profileWrites).toBe(0)
})

test('applies persisted appearance before the first application frame', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('calograph.theme', 'light')
    const capture = () => {
      if (!document.body) {
        requestAnimationFrame(capture)
        return
      }
      const root = document.documentElement
      Object.assign(window, {
        __calographThemeFirstFrame: {
          theme: root.dataset.theme,
          preference: root.dataset.themePreference,
          colorScheme: root.querySelector('meta[name="color-scheme"]')?.getAttribute('content'),
          pageBackground: getComputedStyle(document.body).backgroundColor,
          themeColors: [...root.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')].map((meta) => meta.content),
        },
      })
    }
    requestAnimationFrame(capture)
  })
  await page.emulateMedia({ colorScheme: 'dark' })
  const moduleGate = Promise.withResolvers<void>()
  const moduleRequested = Promise.withResolvers<void>()
  await page.route(/\/assets\/[^/]+\.js(?:\?.*)?$/u, async (route) => {
    moduleRequested.resolve()
    await moduleGate.promise
    await route.continue()
  })
  const navigation = page.goto('/analyse')

  try {
    await moduleRequested.promise
    await page.waitForFunction(() => Boolean(
      (window as Window & { __calographThemeFirstFrame?: { theme?: string } }).__calographThemeFirstFrame,
    ))
    const firstFrame = await page.evaluate(() => (
      window as Window & {
        __calographThemeFirstFrame?: {
          theme?: string
          preference?: string
          colorScheme?: string
          pageBackground?: string
          themeColors?: string[]
        }
      }
    ).__calographThemeFirstFrame)

    expect(firstFrame).toMatchObject({
      theme: 'light',
      preference: 'light',
      colorScheme: 'light',
      pageBackground: 'rgb(246, 247, 251)',
      themeColors: ['#f6f7fb', '#f6f7fb'],
    })
  } finally {
    moduleGate.resolve()
    await navigation
  }
})

test('keeps the operating-system CSS fallback when the synchronous bootstrap is unavailable', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.addInitScript(() => {
    const capture = () => {
      if (!document.body) {
        requestAnimationFrame(capture)
        return
      }
      const root = document.documentElement
      Object.assign(window, {
        __calographThemeFallbackFrame: {
          theme: root.dataset.theme,
          colorScheme: getComputedStyle(root).colorScheme,
          pageBackground: getComputedStyle(document.body).backgroundColor,
        },
      })
    }
    requestAnimationFrame(capture)
  })
  await page.route('**/theme-bootstrap.js', (route) => route.abort())
  const moduleGate = Promise.withResolvers<void>()
  const moduleRequested = Promise.withResolvers<void>()
  await page.route(/\/assets\/[^/]+\.js(?:\?.*)?$/u, async (route) => {
    moduleRequested.resolve()
    await moduleGate.promise
    await route.continue()
  })
  const navigation = page.goto('/')

  try {
    await moduleRequested.promise
    await page.waitForFunction(() => Boolean(
      (window as Window & { __calographThemeFallbackFrame?: { colorScheme?: string } }).__calographThemeFallbackFrame,
    ))
    const firstFrame = await page.evaluate(() => (
      window as Window & {
        __calographThemeFallbackFrame?: {
          theme?: string
          colorScheme?: string
          pageBackground?: string
        }
      }
    ).__calographThemeFallbackFrame)
    expect(firstFrame).toEqual({
      theme: undefined,
      colorScheme: 'light',
      pageBackground: 'rgb(246, 247, 251)',
    })
  } finally {
    moduleGate.resolve()
    await navigation
  }
})
