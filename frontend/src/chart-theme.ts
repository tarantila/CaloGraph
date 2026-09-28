import { computed } from 'vue'

import { effectiveTheme } from './theme'

export interface ChartColors {
  background: string
  grid: string
  axis: string
  label: string
  tooltipBackground: string
  tooltipBorder: string
  tooltipInk: string
  violet: string
  teal: string
  blue: string
  budget: string
  active: string
  baseline: string
  lilac: string
}

export const chartColors = computed<ChartColors>(() => {
  void effectiveTheme.value
  const css = getComputedStyle(document.documentElement)
  const role = (name: string) => css.getPropertyValue(name).trim()

  return {
    background: role('--chart-background'),
    grid: role('--chart-grid'),
    axis: role('--chart-axis'),
    label: role('--chart-label'),
    tooltipBackground: role('--chart-tooltip-bg'),
    tooltipBorder: role('--chart-tooltip-border'),
    tooltipInk: role('--chart-tooltip-ink'),
    violet: role('--chart-series-violet'),
    teal: role('--chart-series-teal'),
    blue: role('--chart-series-blue'),
    budget: role('--chart-series-budget'),
    active: role('--chart-series-active'),
    baseline: role('--chart-series-baseline'),
    lilac: role('--chart-series-lilac'),
  }
})
