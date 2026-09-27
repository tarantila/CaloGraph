import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

const analysisChildren = [
  { path: '', name: 'analysis-overview', component: { template: '<h1>Analyse</h1><button id="period-control">Zeitraum</button>' } },
  { path: 'katalog', name: 'analysis-catalog', component: { template: '<h1>Katalog</h1>' } },
  { path: 'koerper/gewicht', name: 'analysis-weight-detail', component: { template: '<h1>Gewicht</h1>' } },
  { path: 'aktivitaet/aktive-energie', name: 'analysis-active-energy', component: { template: '<h1>Aktive Energie</h1>' } },
  { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: { template: '<h1>Vitamin A</h1>' } },
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

describe('AnalysisLayout route focus', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    setLocale(DEFAULT_LOCALE)
  })

  it('renders the selected page without permanent analysis section navigation', async () => {
    const { wrapper } = await mountAnalysis('/analyse')

    expect(wrapper.get('.analysis-content h1').text()).toBe('Analyse')
    expect(wrapper.find('.analysis-navigation').exists()).toBe(false)
    expect(wrapper.find('.analysis-mobile-navigation').exists()).toBe(false)
    wrapper.unmount()
  })

  it('focuses the heading when a detail route changes', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/katalog')

    await router.push('/analyse/aktivitaet/aktive-energie')
    await flushPromises()

    expect(wrapper.get('.analysis-content h1').text()).toBe('Aktive Energie')
    expect(document.activeElement).toBe(wrapper.get('.analysis-content h1').element)
    wrapper.unmount()
  })

  it('keeps keyboard focus for query-only route updates', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse')
    const periodControl = wrapper.get<HTMLButtonElement>('#period-control').element
    periodControl.focus()

    await router.replace({ query: { period: '7' } })
    await flushPromises()

    expect(document.activeElement).toBe(periodControl)
    wrapper.unmount()
  })

  it('focuses a child heading on direct entry from another route', async () => {
    const { router, wrapper } = await mountAnalysis('/outside')

    await router.push('/analyse/koerper/gewicht')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.get('.analysis-content h1').element)
    wrapper.unmount()
  })
})
