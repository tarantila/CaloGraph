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
})
