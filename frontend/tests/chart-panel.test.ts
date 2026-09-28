import { computed, defineComponent, h, nextTick, ref } from 'vue'

import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { disconnectMock, disposeMock, initMock, observeMock, setOptionMock, useMock } = vi.hoisted(() => ({
  disconnectMock: vi.fn(),
  disposeMock: vi.fn(),
  initMock: vi.fn(),
  observeMock: vi.fn(),
  setOptionMock: vi.fn(),
  useMock: vi.fn(),
}))

vi.mock('echarts/charts', () => ({ BarChart: {}, LineChart: {} }))
vi.mock('echarts/components', () => ({ GridComponent: {}, LegendComponent: {}, TooltipComponent: {} }))
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }))
vi.mock('echarts/core', () => ({
  init: initMock,
  use: useMock,
}))

import ChartPanel from '../src/components/ChartPanel.vue'

class ResizeObserverMock {
  observe = observeMock
  disconnect = disconnectMock
}

import { effectiveTheme, setThemePreference } from '../src/theme'

describe('ChartPanel', () => {
  beforeEach(() => {
    initMock.mockReset()
    observeMock.mockReset()
    disconnectMock.mockReset()
    disposeMock.mockReset()
    setOptionMock.mockReset()
    useMock.mockReset()
    initMock.mockReturnValue({ dispose: disposeMock, setOption: setOptionMock })
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  })

  it('initializes when data replaces the empty state', async () => {
    const wrapper = mount(ChartPanel, {
      props: { title: 'Kalorien', option: {}, empty: true },
    })

    expect(initMock).not.toHaveBeenCalled()

    await wrapper.setProps({ empty: false })
    await flushPromises()

    expect(initMock).toHaveBeenCalledTimes(1)
    expect(setOptionMock).toHaveBeenCalledWith({})
    expect(observeMock).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ empty: true })
    expect(disposeMock).toHaveBeenCalledTimes(1)
    expect(disconnectMock).toHaveBeenCalledTimes(1)
  })

  it('repaints a live chart after theme changes without recreating a disposed instance', async () => {
    setThemePreference('dark')
    let setEmpty!: (empty: boolean) => void
    const Parent = defineComponent({
      setup() {
        const empty = ref(false)
        setEmpty = (value: boolean) => { empty.value = value }
        const option = computed(() => ({
          backgroundColor: effectiveTheme.value === 'dark' ? '#111d2f' : '#ffffff',
        }))
        return () => h(ChartPanel, { title: 'Chart', option: option.value, empty: empty.value })
      },
    })
    const wrapper = mount(Parent)

    expect(initMock).toHaveBeenCalledTimes(1)
    expect(setOptionMock.mock.calls[0][0]).toEqual({ backgroundColor: '#111d2f' })

    setThemePreference('light')
    await nextTick()
    await flushPromises()
    expect(initMock).toHaveBeenCalledTimes(1)
    expect(disposeMock).not.toHaveBeenCalled()
    expect(setOptionMock).toHaveBeenLastCalledWith({ backgroundColor: '#ffffff' }, true)

    setEmpty(true)
    await nextTick()
    const updatesAtDispose = setOptionMock.mock.calls.length
    expect(disposeMock).toHaveBeenCalledTimes(1)

    setThemePreference('dark')
    await nextTick()
    await flushPromises()
    expect(setOptionMock).toHaveBeenCalledTimes(updatesAtDispose)

    setEmpty(false)
    await nextTick()
    await flushPromises()
    expect(initMock).toHaveBeenCalledTimes(2)
    expect(setOptionMock).toHaveBeenCalledTimes(updatesAtDispose + 1)
    wrapper.unmount()
  })

  it('reapplies a static option when the effective theme changes', async () => {
    setThemePreference('dark')
    const wrapper = mount(ChartPanel, {
      props: { title: 'Static chart', option: { backgroundColor: '#111d2f' } },
    })
    const initialOption = setOptionMock.mock.calls[0][0]

    setThemePreference('light')
    await nextTick()
    await flushPromises()

    expect(effectiveTheme.value).toBe('light')
    expect(initMock).toHaveBeenCalledTimes(1)
    expect(disposeMock).not.toHaveBeenCalled()
    expect(setOptionMock).toHaveBeenCalledTimes(2)
    expect(setOptionMock).toHaveBeenLastCalledWith(initialOption, true)
    wrapper.unmount()
  })
})
