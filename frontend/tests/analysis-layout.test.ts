import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

const analysisChildren = [
  { path: '', name: 'analysis-overview', component: { template: '<h1>Analyse</h1>' } },
  { path: 'ernaehrung', name: 'analysis-nutrition', component: { template: '<h1>Ernährung</h1><button id="period-control">Zeitraum</button>' } },
  { path: 'mikronaehrstoffe', name: 'analysis-micronutrients', component: { template: '<h1>Mikronährstoffe</h1>' } },
  { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: { template: '<h1>Vitamin A</h1>' } },
  { path: 'aktivitaet', name: 'analysis-activity', component: { template: '<h1>Aktivität</h1>' } },
  { path: 'koerper', name: 'analysis-body', component: { template: '<h1>Körper</h1>' } },
]

async function mountAnalysis(path = '/analyse') {
  setLocale(DEFAULT_LOCALE)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/outside', name: 'outside', component: { template: '<h1>Außerhalb</h1>' } },
      { path: '/analyse', component: AnalysisLayout, children: analysisChildren },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: { plugins: [router] },
  })
  await flushPromises()
  return { router, wrapper }
}

describe('AnalysisLayout navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    setLocale(DEFAULT_LOCALE)
  })

  it('provides five desktop areas and one labeled mobile selector', async () => {
    const { wrapper } = await mountAnalysis('/analyse/ernaehrung')

    expect(wrapper.findAll('.analysis-navigation a')).toHaveLength(5)
    expect(wrapper.findAll('.analysis-navigation a[aria-current="page"]')).toHaveLength(1)
    expect(wrapper.get('.analysis-content h1').text()).toBe('Ernährung')
    expect(wrapper.get('label[for="analysis-area"]').text()).toContain('Bereich auswählen')
    expect(wrapper.get<HTMLSelectElement>('select[name=\"analysis-area\"]').element.value).toBe('analysis-nutrition')
    wrapper.unmount()
  })

  it('maps detail routes to the micronutrient area and focuses changed destinations', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    const select = wrapper.get<HTMLSelectElement>('select[name="analysis-area"]')

    expect(select.element.value).toBe('analysis-micronutrients')
    expect(wrapper.get('a[href="/analyse/mikronaehrstoffe"]').attributes('aria-current')).toBe('page')
    await select.setValue('analysis-activity')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('analysis-activity')
    expect(select.element.value).toBe('analysis-activity')
    expect(document.activeElement).toBe(wrapper.get('.analysis-content h1').element)
    wrapper.unmount()
  })

  it('keeps keyboard focus in the period control for query-only route updates', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung')
    const periodControl = wrapper.get<HTMLButtonElement>('#period-control').element
    periodControl.focus()

    await router.replace({ query: { start: '2026-03-25', end: '2026-03-31', period: '7' } })
    await flushPromises()

    expect(document.activeElement).toBe(periodControl)
    wrapper.unmount()
  })

  it('focuses a child heading on direct entry from another route', async () => {
    const { router, wrapper } = await mountAnalysis('/outside')

    await router.push('/analyse/koerper')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.get('.analysis-content h1').element)
    wrapper.unmount()
  })
})
