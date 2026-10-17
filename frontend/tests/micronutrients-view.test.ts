import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteLocationRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {},
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import PeriodPicker from '../src/components/PeriodPicker.vue'
import { useAuthStore } from '../src/stores/auth'
import { setLocale } from '../src/i18n'
import type { ReaderPeriodCapabilities } from '../src/types'

enableAutoUnmount(afterEach)

const unsupported: ReaderPeriodCapabilities = { supports_all_data: false, all_data_start_date: null }
const supported: ReaderPeriodCapabilities = { supports_all_data: true, all_data_start_date: '2026-01-01' }

function nutrient(label: string, average = 9.8) {
  return {
    id: 'mineral.iron', metric_type: 'iron_mg', label, category: 'mineral', unit: 'mg',
    eu_nrv: 14, total: average * 20, average_daily: average, days_with_value: 20,
    coverage_ratio: 1, percent_of_nrv: 70, status: 'below_orientation',
  }
}

function response(overrides: Record<string, unknown> = {}) {
  return {
    start_date: '2026-09-02',
    end_date: '2026-10-01',
    source: null,
    recorded_days: 0,
    last_updated_at: null,
    available_sources: [],
    nutrients: [],
    period_capabilities: unsupported,
    definition: { coverage_threshold: 0.7, orientation_threshold_percent: 80 },
    ...overrides,
  }
}

async function mountView(location: RouteLocationRaw = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: MicronutrientsView }],
  })
  await router.push(location)
  await router.isReady()
  const wrapper = mount(MicronutrientsView, {
    global: {
      plugins: [router],
      stubs: { PeriodPicker: false },
    },
  })
  await flushPromises()
  return { router, wrapper }
}

type MicronutrientsWrapper = VueWrapper<InstanceType<typeof MicronutrientsView>>

async function openPicker(wrapper: MicronutrientsWrapper) {
  const picker = wrapper.getComponent(PeriodPicker)
  if (!picker.find('.analysis-period-popover').exists()) {
    await picker.get('.analysis-period-custom-button').trigger('click')
  }
  return picker
}

function requests() {
  return apiMock.mock.calls
    .filter(([path]) => typeof path === 'string' && path.startsWith('/analytics/micronutrients?'))
    .map(([path]) => new URL(path as string, 'https://calograph.test').searchParams)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
  apiMock.mockReset()
  setActivePinia(createPinia())
  useAuthStore().user = {
    id: 'user-1', username: 'owner', language: 'de', timezone: 'Europe/Berlin',
    week_starts_on: 0, raw_payload_retention_days: 0, is_admin: false,
    is_active: true, deactivated_at: null,
  }
  setLocale('de')
})
afterEach(() => vi.useRealTimers())

describe('MicronutrientsView reader capability and source routing', () => {
  it.each([undefined, 'apple_health_xml'])('probes 30 days before resolving initial URL All for source %s', async (source) => {
    const pending = deferred<Record<string, unknown>>()
    apiMock.mockReturnValue(pending.promise)
    const { router, wrapper } = await mountView({
      path: '/', query: { start: '2026-01-01', end: '2026-10-01', period: 'all', source, note: 'keep' },
      hash: '#nutrients',
    })
    expect(requests().map((query) => Object.fromEntries(query))).toEqual([{
      start: '2026-09-02', end: '2026-10-01', ...(source ? { source } : {}),
    }])
    const picker = await openPicker(wrapper)
    expect(picker.find('[data-period="all"]').exists()).toBe(false)
    pending.resolve(response({ source, nutrients: [nutrient('Eisen der aktuellen Quelle')] }))
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-02', end: '2026-10-01', period: '30', ...(source ? { source } : {}), note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
    expect(requests().every((query) => !query.has('period'))).toBe(true)
    expect(picker.find('[data-period="all"]').exists()).toBe(false)
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen der aktuellen Quelle')
  })

  it('restores initial URL All only after a current successful reader probe supplies a valid boundary', async () => {
    const probe = deferred<Record<string, unknown>>()
    apiMock.mockReturnValueOnce(probe.promise).mockResolvedValue(response({
      period_capabilities: supported, nutrients: [nutrient('Eisen aus der gesamten Historie')],
    }))
    const { router, wrapper } = await mountView('/?period=all&source=apple_health_xml&note=keep#nutrients')
    expect(requests().map((query) => Object.fromEntries(query))).toEqual([{
      start: '2026-09-02', end: '2026-10-01', source: 'apple_health_xml',
    }])
    probe.resolve(response({ period_capabilities: supported }))
    await flushPromises()
    expect(requests().at(-1)!.get('period')).toBe('all')
    expect(requests().at(-1)!.get('start')).toBe('2026-01-01')
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-01-01', end: '2026-10-01', period: 'all', source: 'apple_health_xml', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen aus der gesamten Historie')
  })

  it.each([
    { label: 'missing capability', capability: undefined },
    { label: 'explicit unsupported capability', capability: unsupported },
    { label: 'truthy non-boolean support', capability: { supports_all_data: 'true', all_data_start_date: '2026-01-01' } },
    { label: 'missing reader boundary', capability: { supports_all_data: true, all_data_start_date: null } },
    { label: 'impossible reader boundary', capability: { supports_all_data: true, all_data_start_date: '2026-02-30' } },
    { label: 'inverted reader boundary', capability: { supports_all_data: true, all_data_start_date: '2026-10-02' } },
    { label: '3662-day reader boundary', capability: { supports_all_data: true, all_data_start_date: '2016-09-22' } },
  ])('does not infer All from explicit source or provenance with $label', async ({ capability }) => {
    apiMock.mockResolvedValue(response({
      source: 'apple_health_xml', period_capabilities: capability,
      selected_provider: { provider_key: 'google_health', latest_evidence_observed_at: '2026-10-01T10:00:00Z' },
      providers: [{ provider_key: 'google_health', latest_evidence_observed_at: '2026-10-01T10:00:00Z' }],
      last_updated_at: '2026-10-01T10:00:00Z',
      nutrients: [nutrient('Eisen')],
    }))
    const { wrapper, router } = await mountView('/?source=apple_health_xml')
    expect((await openPicker(wrapper)).find('[data-period="all"]').exists()).toBe(false)
    expect(router.currentRoute.value.query.source).toBe('apple_health_xml')
    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('Google Health')
    expect(wrapper.get('.micronutrient-source-filter').text()).not.toContain('Apple Health')
    expect(wrapper.find('.micronutrient-backfill').exists()).toBe(false)
    expect(requests().every((query) => !query.has('period'))).toBe(true)
  })

  it.each(['2026-01-01', '2016-09-23'])('uses the current reader boundary %s for All without an explicit source', async (boundary) => {
    const capability = { supports_all_data: true, all_data_start_date: boundary }
    apiMock.mockImplementation((path: string) => Promise.resolve(response({
      period_capabilities: capability,
      selected_provider: { provider_key: 'google_health', latest_evidence_observed_at: null },
      nutrients: [nutrient(path.includes('period=all') ? 'Eisen aus der gesamten Historie' : 'Eisen aus 30 Tagen')],
    })))
    const { wrapper, router } = await mountView('/?note=keep#nutrients')
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen aus 30 Tagen')
    const picker = await openPicker(wrapper)
    await picker.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(requests().at(-1)!.get('start')).toBe(boundary)
    expect(requests().at(-1)!.get('end')).toBe('2026-10-01')
    expect(requests().at(-1)!.get('period')).toBe('all')
    expect(requests().at(-1)!.has('source')).toBe(false)
    expect(router.currentRoute.value.query).toEqual({
      start: boundary, end: '2026-10-01', period: 'all', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen aus der gesamten Historie')
    expect(wrapper.get('.micronutrient-columns').text()).not.toContain('Eisen aus 30 Tagen')
  })

  it('revokes the old source capability during a source switch and ignores its late response', async () => {
    const oldRequest = deferred<Record<string, unknown>>()
    const currentRequest = deferred<Record<string, unknown>>()
    apiMock.mockResolvedValueOnce(response({
      source: 'apple_health_xml', period_capabilities: supported, nutrients: [nutrient('Altes Eisen')],
    })).mockReturnValueOnce(oldRequest.promise).mockReturnValue(currentRequest.promise)
    const { wrapper, router } = await mountView(
      '/?source=apple_health_xml&period=custom&start=2026-09-01&end=2026-09-30&note=keep#nutrients',
    )
    expect((await openPicker(wrapper)).find('[data-period="all"]').exists()).toBe(true)
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-option[data-period="7"]').trigger('click')
    await flushPromises()
    expect(requests().at(-1)!.get('source')).toBe('apple_health_xml')
    await router.push({
      query: { ...router.currentRoute.value.query, source: undefined }, hash: '#nutrients',
    })
    await flushPromises()
    const picker = await openPicker(wrapper)
    expect(picker.find('[data-period="all"]').exists()).toBe(false)
    expect(requests().at(-1)!.has('source')).toBe(false)
    currentRequest.resolve(response({
      selected_provider: { provider_key: 'google_health', latest_evidence_observed_at: '2026-10-01T10:00:00Z' },
      nutrients: [nutrient('Aktuelles Eisen')], last_updated_at: '2026-10-01T10:00:00Z',
    }))
    await flushPromises()
    oldRequest.resolve(response({
      source: 'apple_health_xml', period_capabilities: supported, nutrients: [nutrient('Verspätetes Eisen')],
      last_updated_at: '2026-09-01T10:00:00Z',
    }))
    await flushPromises()
    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('Google Health')
    expect(wrapper.get('.micronutrient-source-filter').text()).not.toContain('Apple Health')
    expect(wrapper.get('.source-freshness').text()).toContain('01.10.2026')
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Aktuelles Eisen')
    expect(wrapper.text()).not.toContain('Verspätetes Eisen')
    expect(picker.find('[data-period="all"]').exists()).toBe(false)
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-25', end: '2026-10-01', period: '7', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
  })

  it('never sends an old All range to a newly selected source before its capability is known', async () => {
    const nextSource = deferred<Record<string, unknown>>()
    apiMock.mockResolvedValue(response({
      source: 'apple_health_xml', period_capabilities: supported, nutrients: [nutrient('Historisches Eisen')],
    }))
    const { wrapper, router } = await mountView('/?source=apple_health_xml&note=keep#nutrients')
    await (await openPicker(wrapper)).get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    apiMock.mockReturnValue(nextSource.promise)
    const requestsBeforeSwitch = requests().length
    await router.push({ query: { ...router.currentRoute.value.query, source: undefined }, hash: '#nutrients' })
    await flushPromises()
    expect((await openPicker(wrapper)).find('[data-period="all"]').exists()).toBe(false)
    expect(requests().slice(requestsBeforeSwitch).map((query) => Object.fromEntries(query))).toEqual([{
      start: '2026-09-02', end: '2026-10-01',
    }])
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-02', end: '2026-10-01', period: '30', note: 'keep',
    })
    nextSource.resolve(response({
      selected_provider: { provider_key: 'google_health', latest_evidence_observed_at: null },
      nutrients: [nutrient('Eisen der neuen Quelle')],
    }))
    await flushPromises()
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen der neuen Quelle')
    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('Google Health')
    expect(router.currentRoute.value.hash).toBe('#nutrients')
  })

  it.each([
    { label: 'unsupported', capability: unsupported },
    { label: 'missing', capability: undefined },
    { label: 'missing boundary', capability: { supports_all_data: true, all_data_start_date: null } },
    { label: 'overlong boundary', capability: { supports_all_data: true, all_data_start_date: '2016-09-22' } },
  ])('falls back from active All to exactly 30 days when the current response is $label', async ({ capability }) => {
    apiMock.mockResolvedValueOnce(response({ source: 'apple_health_xml', period_capabilities: supported }))
      .mockResolvedValueOnce(response({ source: 'apple_health_xml', period_capabilities: capability }))
      .mockResolvedValue(response({ source: 'apple_health_xml', nutrients: [nutrient('Eisen aus sicherem Zeitraum')] }))
    const { wrapper, router } = await mountView('/?source=apple_health_xml&tracking=complete&note=keep#nutrients')
    await (await openPicker(wrapper)).get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(requests().map((query) => Object.fromEntries(query))).toEqual([
      { start: '2026-09-02', end: '2026-10-01', source: 'apple_health_xml' },
      { start: '2026-01-01', end: '2026-10-01', period: 'all', source: 'apple_health_xml' },
      { start: '2026-09-02', end: '2026-10-01', source: 'apple_health_xml' },
    ])
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-02', end: '2026-10-01', period: '30',
      source: 'apple_health_xml', tracking: 'complete', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text()).toContain('30 Tage')
    expect((await openPicker(wrapper)).find('[data-period="all"]').exists()).toBe(false)
    expect(wrapper.get('.micronutrient-columns').text()).toContain('Eisen aus sicherem Zeitraum')
  })

  it('preserves explicit source provenance, freshness and history sync with the real picker mounted', async () => {
    const sync = deferred<{ inserted: number; updated: number; skipped: number }>()
    let synced = false
    apiMock.mockImplementation((path: string) => {
      if (path === '/yazio/sync?days=60') {
        synced = true
        return sync.promise
      }
      return Promise.resolve(response({
        source: 'yazio_export_v1', recorded_days: 20,
        last_updated_at: synced ? '2026-10-01T10:30:00Z' : '2026-09-30T10:30:00Z',
        nutrients: [nutrient('Eisen', synced ? 14 : 9.8)],
      }))
    })
    const { wrapper, router } = await mountView(
      '/?source=yazio_export_v1&start=2026-09-01&end=2026-09-30&period=custom&note=keep#nutrients',
    )
    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('YAZIO')
    expect(wrapper.get('.source-freshness').text()).toContain('30.09.2026')
    expect(wrapper.get('.nutrient-value').text()).toContain('9,8 mg')
    const button = wrapper.get('.micronutrient-backfill button')
    await button.trigger('click')
    expect(button.attributes('disabled')).toBeDefined()
    expect(apiMock).toHaveBeenLastCalledWith('/yazio/sync?days=60', { method: 'POST' })
    sync.resolve({ inserted: 2, updated: 1, skipped: 4 })
    await flushPromises()
    expect(wrapper.get('.micronutrient-backfill button').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('.micronutrient-sync-message').text()).toContain('3')
    expect(wrapper.get('.source-freshness').text()).toContain('01.10.2026')
    expect(wrapper.get('.nutrient-value').text()).toContain('14 mg')
    expect(router.currentRoute.value.query).toEqual({
      source: 'yazio_export_v1', start: '2026-09-01', end: '2026-09-30', period: 'custom', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrients')
    expect((await openPicker(wrapper)).find('[data-period="all"]').exists()).toBe(false)
  })
})
