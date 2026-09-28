import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chartColors } from '../src/chart-theme'
import { setThemePreference } from '../src/theme'

const roleNames = [
  '--chart-background',
  '--chart-grid',
  '--chart-axis',
  '--chart-label',
  '--chart-tooltip-bg',
  '--chart-tooltip-border',
  '--chart-tooltip-ink',
  '--chart-series-violet',
  '--chart-series-teal',
  '--chart-series-blue',
  '--chart-series-budget',
  '--chart-series-active',
  '--chart-series-baseline',
  '--chart-series-lilac',
] as const

function installChartStyleReader() {
  const calls = vi.fn()
  vi.stubGlobal('getComputedStyle', (element: Element) => {
    calls(element)
    const theme = element.getAttribute('data-theme') ?? 'dark'
    return {
      getPropertyValue: (name: string) => roleNames.includes(name as (typeof roleNames)[number])
        ? `${theme}-${name.slice('--chart-'.length)}`
        : '',
    } as CSSStyleDeclaration
  })
  return calls
}

describe('chart theme palette', () => {
  beforeEach(() => {
    document.documentElement.dataset.theme = 'dark'
    setThemePreference('dark')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    setThemePreference('system')
  })

  it('samples every semantic canvas role and refreshes only when the effective theme changes', () => {
    const readStyles = installChartStyleReader()
    const dark = chartColors.value

    expect(dark).toEqual({
      background: 'dark-background',
      grid: 'dark-grid',
      axis: 'dark-axis',
      label: 'dark-label',
      tooltipBackground: 'dark-tooltip-bg',
      tooltipBorder: 'dark-tooltip-border',
      tooltipInk: 'dark-tooltip-ink',
      violet: 'dark-series-violet',
      teal: 'dark-series-teal',
      blue: 'dark-series-blue',
      budget: 'dark-series-budget',
      active: 'dark-series-active',
      baseline: 'dark-series-baseline',
      lilac: 'dark-series-lilac',
    })
    expect(readStyles).toHaveBeenCalledTimes(1)
    expect(chartColors.value).toBe(dark)
    expect(readStyles).toHaveBeenCalledTimes(1)

    setThemePreference('light')
    document.documentElement.dataset.theme = 'light'
    const light = chartColors.value
    expect(light.grid).toBe('light-grid')
    expect(light.tooltipBackground).toBe('light-tooltip-bg')
    expect(light.violet).toBe('light-series-violet')
    expect(light).not.toBe(dark)
    expect(readStyles).toHaveBeenCalledTimes(2)
    expect(Object.values(light).every((value) => !value.startsWith('var('))).toBe(true)
  })
})
