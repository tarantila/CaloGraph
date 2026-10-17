import { expect, test, type Locator, type Page, type Response } from '@playwright/test'

test.use({ trace: 'off', locale: 'de-DE' })

const demoUsername = 'calograph-analysis-demo'
const dayMs = 86_400_000
const readerPaths: Record<string, string> = {
  'analytics.weight': '/api/v1/analytics/weight',
  'analytics.verification.activity': '/api/v1/analytics/verification/activity',
  'analytics.nutrition.analysis': '/api/v1/analytics/nutrition/analysis',
  'analytics.nutrition.micronutrients': '/api/v1/analytics/nutrition/micronutrients',
}
const routes: Array<{ path: string; reader: string | null }> = [
  { path: '/', reader: '/analytics/trends' },
  { path: '/tage', reader: '/analytics/daily' },
  { path: '/trends', reader: '/analytics/trends' },
  { path: '/wochentage', reader: '/analytics/weekdays' },
  { path: '/mikronaehrstoffe', reader: '/analytics/micronutrients' },
  { path: '/analyse', reader: null },
  { path: '/analyse/katalog', reader: '/analytics/nutrition/micronutrients' },
  { path: '/analyse/koerper', reader: null },
  { path: '/analyse/aktivitaet', reader: '/analytics/verification/activity' },
  { path: '/analyse/ernaehrung', reader: '/analytics/nutrition/analysis' },
  { path: '/analyse/mikronaehrstoffe', reader: '/analytics/nutrition/micronutrients' },
  { path: '/analyse/koerper/gewicht', reader: '/analytics/weight' },
  { path: '/analyse/aktivitaet/aktive-energie', reader: '/analytics/verification/activity' },
  { path: '/analyse/ernaehrung/dietary_energy_kcal', reader: '/analytics/nutrition/analysis' },
  { path: '/analyse/mikronaehrstoffe/vitamin_a_ug', reader: '/analytics/nutrition/micronutrients' },
]

function safeBaseUrl(): URL {
  if (process.env.CALOGRAPH_ANALYSIS_DEMO_SAFE_RUNTIME !== '1'
    || !process.env.BASE_URL || !process.env.CALOGRAPH_ANALYSIS_DEMO_PASSWORD) {
    throw new Error('Browserabnahme blockiert: bestätigte isolierte Demo-Runtime und private Demo-Anmeldung fehlen.')
  }
  let base: URL
  try {
    base = new URL(process.env.BASE_URL)
  } catch {
    throw new Error('Browserabnahme blockiert: BASE_URL ist keine gültige lokale URL.')
  }
  if (!['http:', 'https:'].includes(base.protocol)
    || !['localhost', '127.0.0.1'].includes(base.hostname)
    || base.username || base.password) {
    throw new Error('Browserabnahme blockiert: BASE_URL muss eine lokale HTTP(S)-Demo-Runtime ohne URL-Anmeldedaten bezeichnen.')
  }
  return base
}

function localUrl(path: string): string {
  return new URL(path, safeBaseUrl()).href
}

test.beforeEach(async ({ page }) => {
  if (process.env.CALOGRAPH_ANALYSIS_DEMO_SAFE_RUNTIME !== '1') {
    test.skip(true, 'Requires an explicitly enabled isolated analysis demo runtime.')
    return
  }
  const base = safeBaseUrl()
  // Absolute URLs avoid the runner's default target; redirects never receive credentials.
  try {
    const login = await page.request.post(new URL('/api/v1/auth/login', base).href, {
      data: { username: demoUsername, password: process.env.CALOGRAPH_ANALYSIS_DEMO_PASSWORD },
      maxRedirects: 0,
    })
    if (login.status() !== 200) throw new Error('login failed')
    await login.dispose()
    const me = await page.request.get(new URL('/api/v1/auth/me', base).href, { maxRedirects: 0 })
    if (me.status() !== 200 || (await me.json()).username !== demoUsername) throw new Error('identity failed')
    await me.dispose()
  } catch {
    // Never propagate request diagnostics, credentials, or the authentication payload.
    throw new Error('Browserabnahme blockiert: echte Anmeldung als dedizierter Demo-Nutzer konnte nicht bestätigt werden.')
  }
})

function inclusiveDays(url: URL): number {
  const start = url.searchParams.get('start')
  const end = url.searchParams.get('end')
  if (!start || !end) return Number.NaN
  return (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / dayMs + 1
}

function periodResponse(page: Page, path: string, days: number): Promise<Response> {
  return page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET' && url.pathname === path && inclusiveDays(url) === days
  })
}

function rangeResponse(page: Page, path: string, start: string, end: string): Promise<Response> {
  return page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET' && url.pathname === path
      && url.searchParams.get('start') === start && url.searchParams.get('end') === end
  })
}

async function demoTimezone(page: Page): Promise<string> {
  const profile = await page.request.get(localUrl('/api/v1/auth/me'), { maxRedirects: 0 })
  if (profile.status() !== 200) {
    await profile.dispose()
    throw new Error('Analysis-Zeitraumtest konnte das authentifizierte Demo-Profil nicht lesen.')
  }
  const identity = await profile.json() as { username?: string; timezone?: string }
  await profile.dispose()
  if (identity.username !== demoUsername || !identity.timezone) {
    throw new Error('Analysis-Zeitraumtest konnte die Demo-Zeitzone nicht bestätigen.')
  }
  return identity.timezone
}

function todayInTimeZone(timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}`
}

function rangeEndingAt(end: string, days: number): { start: string; end: string } {
  const startDate = new Date(Date.parse(`${end}T00:00:00Z`) - (days - 1) * dayMs)
  return { start: startDate.toISOString().slice(0, 10), end }
}

function germanDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-')
  return `${day}.${month}.${year}`
}

async function noOverflow(page: Page, popover?: Locator): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false)
  if (popover) {
    expect(await popover.evaluate((element) => {
      const box = element.getBoundingClientRect()
      return element.scrollWidth > element.clientWidth + 1 || box.left < -1 || box.right > window.innerWidth + 1
    })).toBe(false)
  }
}

async function openPicker(page: Page, width: number): Promise<{ picker: Locator; popover: Locator }> {
  const picker = page.locator('.analysis-period-picker')
  await expect(picker).toBeVisible()
  if (width > 600) {
    for (const preset of ['7', '30', '90']) {
      await expect(picker.locator(`.analysis-period-strip [data-period="${preset}"]`)).toBeVisible()
    }
    await expect(picker.locator('.analysis-period-strip [data-period="30"]')).toHaveAttribute('aria-pressed', 'true')
    await picker.locator('.analysis-period-custom-button').click()
  } else {
    await expect(picker.locator('.analysis-period-trigger')).toContainText('30')
    await picker.locator('.analysis-period-trigger').click()
  }
  const popover = picker.locator('.analysis-period-popover')
  await expect(popover).toBeVisible()
  await expect(popover.locator('[data-period="30"]')).toHaveAttribute('aria-pressed', 'true')
  await expect(popover.getByRole('button', { name: 'Zeitraum anwenden', exact: true })).toBeVisible()
  await expect(popover.locator('button:disabled')).toHaveCount(0)
  await expect(picker.locator('[data-period="all"]')).toHaveCount(0)
  const columns = await popover.locator('.analysis-period-layout').evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/u).length,
  )
  expect(columns).toBe(width > 600 ? 2 : 1)
  await noOverflow(page, popover)
  return { picker, popover }
}

type RegistryEntry = {
  analysis_key: string
  reader_key: string
  status: string
  card_capable: boolean
}

async function selectedHubReaders(preferencesResponse: Response, registryResponse: Response): Promise<string[]> {
  expect(preferencesResponse.status()).toBe(200)
  expect(registryResponse.status()).toBe(200)
  const preferences = await preferencesResponse.json() as { active_card_ids: string[] }
  const registry = await registryResponse.json() as { analyses: RegistryEntry[] }
  expect(Array.isArray(preferences.active_card_ids)).toBe(true)
  expect(Array.isArray(registry.analyses)).toBe(true)
  const selected = new Set(preferences.active_card_ids.flatMap((id) =>
    id === 'weight' ? ['weight_kg'] : id === 'activity' ? ['active_energy_kcal'] : [id]))
  const keys = new Set([
    ...Object.keys(readerPaths),
    ...registry.analyses
      .filter((entry) => selected.has(entry.analysis_key) && entry.status === 'available' && entry.card_capable === true)
      .map((entry) => entry.reader_key),
  ])
  return [...keys].map((key) => {
    if (!Object.hasOwn(readerPaths, key)) throw new Error(`Unbekannter ausgewählter Analysis-Reader: ${key}`)
    return readerPaths[key]!
  })
}

for (const width of [1440, 820, 768, 390, 320]) {
  test(`reale Produktzeiträume auf allen 15 Routen bei ${width}px`, async ({ page }) => {
    test.setTimeout(180_000)
    await page.setViewportSize({ width, height: 900 })
    for (const route of routes) {
      const isHub = route.path === '/analyse'
      const preferences = isHub ? page.waitForResponse((response) =>
        new URL(response.url()).pathname === '/api/v1/settings/analysis-preferences'
        && response.request().method() === 'GET') : null
      const registry = isHub ? page.waitForResponse((response) =>
        new URL(response.url()).pathname === '/api/v1/analytics/registry'
        && response.request().method() === 'GET') : null
      const initialResponse = route.reader ? periodResponse(page, `/api/v1${route.reader}`, 30) : null
      await page.goto(localUrl(route.path))
      if (initialResponse) expect((await initialResponse).status()).toBe(200)
      const expectedReaders = preferences && registry
        ? await selectedHubReaders(await preferences, await registry)
        : route.reader ? [`/api/v1${route.reader}`] : []
      const { popover } = await openPicker(page, width)
      await page.screenshot({ path: test.info().outputPath(`period-open-${width}-${encodeURIComponent(route.path)}.png`), fullPage: true })
      const received = new Set<string>()
      const onResponse = (response: Response): void => {
        const url = new URL(response.url())
        if (response.request().method() === 'GET' && expectedReaders.includes(url.pathname)
          && inclusiveDays(url) === 7 && response.status() === 200) received.add(url.pathname)
      }
      page.on('response', onResponse)
      try {
        await popover.locator('[data-period="7"]').click()
        await expect.poll(() => new URL(page.url()).searchParams.get('period')).toBe('7')
        await expect(popover).not.toBeVisible()
        // Category-only routes and a hub with no selected readers have nothing to wait for.
        if (expectedReaders.length) {
          await expect.poll(() => expectedReaders.filter((path) => !received.has(path)), { timeout: 30_000 }).toEqual([])
        }
        if (isHub) {
          const detailLinks = page.locator('.analysis-card a[href]')
          for (const href of await detailLinks.evaluateAll((links) => links.map((link) => (link as HTMLAnchorElement).href))) {
            expect(new URL(href).searchParams.get('period')).toBe('7')
          }
        }
        await noOverflow(page)
        await page.screenshot({ path: test.info().outputPath(`period-${width}-${encodeURIComponent(route.path)}.png`), fullPage: true })
      } finally {
        page.off('response', onResponse)
      }
    }
  })
}

test('Aktivität behält kanonische Sieben-Tage-Fenster ohne gemeinsamen Zeitraumwähler', async ({ page }) => {
  const initialResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET'
      && url.pathname === '/api/v1/analytics/verification/activity'
      && url.searchParams.get('view') === 'canonical' && inclusiveDays(url) === 7
  })
  await page.goto(localUrl('/aktivitaet'))
  const initial = await initialResponse
  expect(initial.status()).toBe(200)
  const initialUrl = new URL(initial.url())
  const nextStart = new Date(Date.parse(`${initialUrl.searchParams.get('start')}T00:00:00Z`) + 7 * dayMs).toISOString().slice(0, 10)
  const nextEnd = new Date(Date.parse(`${initialUrl.searchParams.get('end')}T00:00:00Z`) + 7 * dayMs).toISOString().slice(0, 10)
  await expect(page.locator('.analysis-period-picker')).toHaveCount(0)
  const nextResponse = page.waitForResponse((response) => {
    const url = new URL(response.url())
    return response.request().method() === 'GET'
      && url.pathname === '/api/v1/analytics/verification/activity'
      && url.searchParams.get('view') === 'canonical' && inclusiveDays(url) === 7
      && url.searchParams.get('start') === nextStart && url.searchParams.get('end') === nextEnd
  })
  await page.getByRole('button', { name: 'Nächste 7 Tage', exact: true }).click()
  expect((await nextResponse).status()).toBe(200)
  await expect(page.locator('.analysis-period-picker')).toHaveCount(0)
})

test('Wochen nutzt den impliziten Backendzeitraum ohne gemeinsamen Zeitraumwähler', async ({ page }) => {
  const weeklyResponse = page.waitForResponse((response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/v1/analytics/weekly')
  await page.goto(localUrl('/wochen'))
  const weekly = await weeklyResponse
  expect(weekly.status()).toBe(200)
  const url = new URL(weekly.url())
  expect(url.searchParams.has('start')).toBe(false)
  expect(url.searchParams.has('end')).toBe(false)
  await expect(page.locator('.analysis-period-picker')).toHaveCount(0)
})

test('Dashboardkennzahlen und laufende Wochenzusammenfassung bleiben bei sieben Trendtagen unverändert', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const initialResponse = periodResponse(page, '/api/v1/analytics/trends', 30)
  const summaryResponse = page.waitForResponse((response) =>
    response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/v1/dashboard/summary')
  await page.goto(localUrl('/'))
  const initialTrends = await initialResponse
  const summary = await summaryResponse
  expect(initialTrends.status()).toBe(200)
  expect(summary.status()).toBe(200)
  await Promise.all([initialTrends.finished(), summary.finished()])
  const metricValues = page.locator('.dashboard-stats .metric-card-value')
  const weeklySummary = page.locator('.weekly-summary-card')
  await expect(metricValues).toHaveCount(4)
  await expect(metricValues.first()).toBeVisible()
  await expect(weeklySummary).toBeVisible()
  // The real Summary response and Vue's populated dashboard must render before the baseline.
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  const metricValuesText = await metricValues.allTextContents()
  const weeklySummaryText = await weeklySummary.innerText()
  const { popover } = await openPicker(page, 1440)
  const trendsResponse = periodResponse(page, '/api/v1/analytics/trends', 7)
  await popover.locator('[data-period="7"]').click()
  const trends = await trendsResponse
  expect(trends.status()).toBe(200)
  await trends.finished()
  await expect.poll(() => new URL(page.url()).searchParams.get('period')).toBe('7')
  await expect(popover).not.toBeVisible()
  // Let the real response update Vue and paint before comparing Summary-backed values.
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await expect(metricValues.first()).toBeVisible()
  await expect(weeklySummary).toBeVisible()
  expect(await metricValues.allTextContents()).toEqual(metricValuesText)
  expect(await weeklySummary.innerText()).toBe(weeklySummaryText)
})

const analysis365Scenarios = [
  { label: '90 rolling days', period: '90', days: 90 },
  { label: '180 rolling days', period: '180', days: 180 },
  { label: '365 rolling days', period: '365', days: 365 },
  { label: '365 custom leap-year days', period: 'custom', days: 365 },
] as const

for (const scenario of analysis365Scenarios) {
  test(`Analysis 365: ${scenario.label} survives canonical navigation, reload and history`, async ({ page }) => {
    test.setTimeout(180_000)
    await page.setViewportSize({ width: 1440, height: 900 })
    const timezone = await demoTimezone(page)
    const today = todayInTimeZone(timezone)
    const range = scenario.period === 'custom'
      ? { start: '2023-04-01', end: '2024-03-30' }
      : rangeEndingAt(today, scenario.days)
    const params = new URLSearchParams({ period: scenario.period, source: 'synthetic' })
    if (scenario.period === 'custom') {
      params.set('start', range.start)
      params.set('end', range.end)
    }
    const initialPath = `/analyse?${params.toString()}#analysis-category-fluid`
    const readerPathsForHub = [
      '/api/v1/analytics/weight',
      '/api/v1/analytics/verification/activity',
      '/api/v1/analytics/nutrition/analysis',
      '/api/v1/analytics/nutrition/micronutrients',
    ]
    const initialReads = readerPathsForHub.map((path) => periodResponse(page, path, scenario.days))
    await page.goto(localUrl(initialPath))
    const fixedResponses = await Promise.all(initialReads)

    const readRange = async (
      response: Response, expectedRange: { start: string; end: string }, expectedDays = scenario.days,
    ): Promise<{ start_date?: string; end_date?: string; period_days?: number }> => {
      expect(response.status()).toBe(200)
      const payload = await response.json() as { start_date?: string; end_date?: string; period_days?: number }
      expect([payload.start_date, payload.end_date]).toEqual([expectedRange.start, expectedRange.end])
      if (payload.period_days !== undefined) expect(payload.period_days).toBe(expectedDays)
      return payload
    }
    const hubPayloads = await Promise.all(fixedResponses.map((response) => readRange(response, range)))
    expect(hubPayloads[2]?.period_days).toBe(scenario.days)
    expect(hubPayloads[3]?.period_days).toBe(scenario.days)
    const assertUrlRange = (expectedPeriod: string, expectedRange: { start: string; end: string }) => {
      const url = new URL(page.url())
      expect(url.searchParams.get('period')).toBe(expectedPeriod)
      expect(url.searchParams.get('source')).toBe('synthetic')
      expect(url.hash).toBe('#analysis-category-fluid')
      if (expectedPeriod === 'custom') {
        expect([url.searchParams.get('start'), url.searchParams.get('end')])
          .toEqual([expectedRange.start, expectedRange.end])
      } else {
        expect(url.searchParams.has('start')).toBe(false)
        expect(url.searchParams.has('end')).toBe(false)
      }
    }
    assertUrlRange(scenario.period, range)

    const categoryRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', scenario.days)
    await page.locator('.analysis-card-list [data-card-key="micronutrients"] .analysis-card-heading a').click()
    await readRange(await categoryRead, range)
    await expect(page).toHaveURL(/\/analyse\/mikronaehrstoffe(?:\?|#|$)/u)
    assertUrlRange(scenario.period, range)
    const picker = page.locator('.analysis-period-picker')
    await expect(picker.locator('.analysis-period-strip [data-period="90"]')).toBeEnabled()
    await picker.locator('.analysis-period-custom-button').click()
    const popover = picker.locator('.analysis-period-popover')
    await expect(popover.locator('[data-period="365"]')).toBeVisible()
    await expect(popover.locator('[data-period="all"]')).toHaveCount(0)
    await page.keyboard.press('Escape')

    const metricLink = page.locator('a[href^="/analyse/mikronaehrstoffe/"]').first()
    const detailRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', scenario.days)
    await metricLink.click()
    await readRange(await detailRead, range)
    await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/analyse\/mikronaehrstoffe\/[^/]+$/u)
    assertUrlRange(scenario.period, range)

    const reloadRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', scenario.days)
    await page.reload()
    await readRange(await reloadRead, range)
    assertUrlRange(scenario.period, range)

    const categoryBreadcrumb = page.locator('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe?"]').first()
    const categoryBreadcrumbRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', scenario.days)
    await categoryBreadcrumb.click()
    await readRange(await categoryBreadcrumbRead, range)
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/mikronaehrstoffe')
    assertUrlRange(scenario.period, range)
    await page.goBack()
    await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/analyse\/mikronaehrstoffe\/[^/]+$/u)
    assertUrlRange(scenario.period, range)
    await page.goForward()
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/mikronaehrstoffe')

    await page.goBack()
    await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/analyse\/mikronaehrstoffe\/[^/]+$/u)
    const hubBreadcrumb = page.locator('.analysis-breadcrumbs a[href^="/analyse?"]').first()
    const hubReads = readerPathsForHub.map((path) => periodResponse(page, path, scenario.days))
    await hubBreadcrumb.click()
    const breadcrumbResponses = await Promise.all(hubReads)
    await Promise.all(breadcrumbResponses.map((response) => readRange(response, range)))
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
    assertUrlRange(scenario.period, range)
    await page.goBack()
    await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/analyse\/mikronaehrstoffe\/[^/]+$/u)
    assertUrlRange(scenario.period, range)
    await page.goForward()
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')

    const summaryLink = page.locator('.analysis-card-list [data-card-key="micronutrients"] .analysis-card-heading a')
    const categoryPeriodRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', scenario.days)
    await summaryLink.click()
    await readRange(await categoryPeriodRead, range)
    const nextPeriod = scenario.period === '365' ? '90' : '365'
    const nextDays = nextPeriod === '90' ? 90 : 365
    const nextRange = rangeEndingAt(today, nextDays)
    await picker.locator('.analysis-period-custom-button').click()
    const replacementRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', nextDays)
    await picker.locator(`.analysis-period-option[data-period="${nextPeriod}"]`).click()
    await readRange(await replacementRead, nextRange, nextDays)
    assertUrlRange(nextPeriod, nextRange)
    await page.goBack()
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
    assertUrlRange(scenario.period, range)
    await page.goForward()
    await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/mikronaehrstoffe')
    assertUrlRange(nextPeriod, nextRange)

    if (scenario.period === '90') {
      const hub90Range = rangeEndingAt(today, 90)
      const historyHubReads = readerPathsForHub.map((path) => periodResponse(page, path, 90))
      await page.goto(localUrl('/analyse?period=90&source=synthetic#analysis-category-fluid'))
      const historyHubResponses = await Promise.all(historyHubReads)
      await Promise.all(historyHubResponses.map((response) => readRange(response, hub90Range, 90)))

      const historySummary = page.locator('.analysis-card-list [data-card-key="micronutrients"] .analysis-card-heading a')
      const historyOverviewRead = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', 90)
      await historySummary.click()
      await readRange(await historyOverviewRead, hub90Range, 90)
      await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/mikronaehrstoffe')
      assertUrlRange('90', hub90Range)

      const custom365 = { start: '2023-04-01', end: '2024-03-30' }
      const custom365Read = periodResponse(page, '/api/v1/analytics/nutrition/micronutrients', 365)
      await picker.locator('.analysis-period-custom-button').click()
      const customDates = picker.locator('.analysis-period-custom-form input[type="text"]')
      await customDates.nth(0).fill(germanDate(custom365.start))
      await customDates.nth(1).fill(germanDate(custom365.end))
      await picker.getByRole('button', { name: 'Zeitraum anwenden', exact: true }).click()
      await readRange(await custom365Read, custom365, 365)
      assertUrlRange('custom', custom365)

      await page.goBack()
      await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse')
      assertUrlRange('90', hub90Range)
      await page.goForward()
      await expect.poll(() => new URL(page.url()).pathname).toBe('/analyse/mikronaehrstoffe')
      assertUrlRange('custom', custom365)
    }

    const oversizedRange = rangeEndingAt(today, 366)
    const invalidUrl = page.url()
    const oversizedRead = page.waitForRequest((request) => {
      const url = new URL(request.url())
      return url.pathname === '/api/v1/analytics/nutrition/micronutrients' && inclusiveDays(url) === 366
    }, { timeout: 1000 }).then(() => true, () => false)
    await picker.locator('.analysis-period-custom-button').click()
    const dates = picker.locator('.analysis-period-custom-form input[type="text"]')
    await dates.nth(0).fill(germanDate(oversizedRange.start))
    await dates.nth(1).fill(germanDate(oversizedRange.end))
    await picker.getByRole('button', { name: 'Zeitraum anwenden', exact: true }).click()
    await expect(picker.getByRole('alert')).toBeVisible()
    await expect(picker.getByRole('alert')).toContainText('365')
    expect(page.url()).toBe(invalidUrl)
    expect(await oversizedRead).toBe(false)

    if (scenario.period === '365') {
      const opener = picker.locator('.analysis-period-custom-button')
      await page.keyboard.press('Escape')
      await expect(opener).toBeFocused()
      await opener.press('Enter')
      await expect(dates.nth(0)).toBeFocused()
      await page.keyboard.press('Tab')
      await expect(picker.locator('.date-input-picker-button').nth(0)).toBeFocused()
      await page.keyboard.press('Tab')
      await expect(dates.nth(1)).toBeFocused()
      const focusOutline = await picker.locator('.date-input').nth(1).evaluate((element) => {
        const style = getComputedStyle(element)
        return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0
          && style.outlineColor !== 'transparent' && style.outlineColor !== 'rgba(0, 0, 0, 0)'
      })
      expect(focusOutline).toBe(true)
      await page.keyboard.press('Escape')
      await expect(opener).toBeFocused()
    }
  })
}

test('freie Tagesrange erhält Filter und lange individuelle Mobilgrenzen bleiben lesbar', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(localUrl('/tage?source=apple_health_xml&tracking=complete,probably_complete&weekday=2#records'))
  const dailyPicker = page.locator('.analysis-period-picker')
  await dailyPicker.locator('.analysis-period-custom-button').click()
  const dailyPopover = dailyPicker.locator('.analysis-period-popover')
  const dates = dailyPopover.locator('input[type="text"]')
  await dates.nth(0).fill('01.03.2024')
  await dates.nth(1).fill('30.03.2024')
  const dailyResponse = rangeResponse(page, '/api/v1/analytics/daily', '2024-03-01', '2024-03-30')
  await dailyPopover.getByRole('button', { name: 'Zeitraum anwenden', exact: true }).click()
  expect((await dailyResponse).status()).toBe(200)
  await expect.poll(() => new URL(page.url()).searchParams.get('period')).toBe('custom')
  const saved = new URL(page.url())
  expect(saved.searchParams.get('start')).toBe('2024-03-01')
  expect(saved.searchParams.get('end')).toBe('2024-03-30')
  expect(saved.searchParams.get('source')).toBe('apple_health_xml')
  expect(saved.searchParams.get('tracking')).toBe('complete,probably_complete')
  expect(saved.searchParams.get('weekday')).toBe('2')
  expect(saved.hash).toBe('#records')
  await page.setViewportSize({ width: 320, height: 900 })
  const weightResponse = rangeResponse(page, '/api/v1/analytics/weight', '2018-01-01', '2026-03-30')
  await page.goto(localUrl('/analyse/koerper/gewicht?period=custom&start=2018-01-01&end=2026-03-30'))
  expect((await weightResponse).status()).toBe(200)
  const weightPicker = page.locator('.analysis-period-picker')
  const trigger = weightPicker.locator('.analysis-period-trigger')
  for (const text of ['Individuell', '01.01.2018', '30.03.2026']) await expect(trigger).toContainText(text)
  expect(await trigger.locator('span').first().evaluate((element) => {
    const text = element.firstChild
    if (!text) return false
    const range = document.createRange()
    range.selectNodeContents(element)
    const button = element.parentElement!.getBoundingClientRect()
    const style = getComputedStyle(element)
    return style.visibility === 'visible' && style.textOverflow !== 'ellipsis'
      && Array.from(range.getClientRects()).every((box) => box.width > 0 && box.left >= button.left - 1 && box.right <= button.right + 1)
      && element.scrollWidth <= element.clientWidth + 1
  })).toBe(true)
  await trigger.click()
  const weightPopover = weightPicker.locator('.analysis-period-popover')
  await expect(weightPopover).toBeVisible()
  await expect(weightPopover.locator('input[type="text"]').nth(0)).toHaveValue('01.01.2018')
  await expect(weightPopover.locator('input[type="text"]').nth(1)).toHaveValue('30.03.2026')
  await noOverflow(page, weightPopover)
  await page.screenshot({ path: test.info().outputPath('period-long-custom-320.png'), fullPage: true })
})
