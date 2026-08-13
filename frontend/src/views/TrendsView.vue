<script setup lang="ts">
import {
  PhBarbell,
  PhCalendarBlank,
  PhChartBar,
  PhTrendUp,
} from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, localizeApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, formatGermanDayMonth } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import type { DailyPoint } from '../types'

const t = i18n.global.t.bind(i18n.global)
const points = ref<DailyPoint[]>([])
const loading = ref(true)
const error = ref('')
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const decimal = createNumberFormatter({ maximumFractionDigits: 1 })

async function load() {
  loading.value = true
  error.value = ''
  try {
    points.value = (await api<{ points: DailyPoint[] }>('/analytics/trends')).points
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}
onMounted(load)

const latestNutrition = computed(() =>
  [...points.value].reverse().find((item) => item.calories_kcal != null),
)
const recordedDays = computed(() => points.value.filter((item) => item.calories_kcal != null).length)
const latestSevenDayAverage = computed(() =>
  [...points.value].reverse().find((item) => item.average_7d != null)?.average_7d ?? null,
)
const rangeLabel = computed(() => {
  if (!points.value.length) return t('common.noData')
  const first = formatGermanDayMonth(points.value[0].date)
  const last = formatGermanDate(points.value.at(-1)!.date)
  return `${first} – ${last}`
})

const tooltip = {
  trigger: 'axis' as const,
  backgroundColor: '#111d30',
  borderColor: '#324157',
  textStyle: { color: '#f3f6fb' },
}
const legendText = { color: '#98a5b9', fontFamily: 'Inter' }
const axisLine = { lineStyle: { color: '#263449' } }
const axisLabel = { color: '#98a5b9', fontFamily: 'Inter' }
const splitLine = { lineStyle: { color: '#263449', type: 'dashed' as const } }

const calorieOption = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: { ...tooltip, valueFormatter: (value) => `${integer.format(Number(value))} ${t('common.kcal')}` },
  legend: {
    top: 0,
    right: 0,
    data: [t('charts.calories'), t('trends.average7'), t('trends.average14'), t('trends.average28'), t('charts.dailyBudget')],
    textStyle: legendText,
    itemWidth: 14,
    itemHeight: 8,
  },
  grid: { left: 58, right: 18, top: 54, bottom: 38 },
  xAxis: {
    type: 'category',
    data: points.value.map((item) => formatGermanDayMonth(item.date)),
    axisLine,
    axisTick: { show: false },
    axisLabel: { ...axisLabel, hideOverlap: true },
  },
  yAxis: { type: 'value', name: t('common.kcal'), nameTextStyle: axisLabel, axisLabel, splitLine },
  series: [
    {
      name: t('charts.calories'),
      type: 'bar',
      barMaxWidth: 24,
      data: points.value.map((item) => item.calories_kcal),
      itemStyle: { color: '#8b5cf6', borderRadius: [4, 4, 0, 0] },
    },
    {
      name: t('trends.average7'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_7d),
      lineStyle: { color: '#2dd4bf', width: 3 },
    },
    {
      name: t('trends.average14'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_14d),
      lineStyle: { color: '#5b8ff9', width: 2 },
    },
    {
      name: t('trends.average28'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_28d),
      lineStyle: { color: '#d19bff', width: 2 },
    },
    {
      name: t('charts.dailyBudget'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.target_kcal),
      lineStyle: { color: '#fb923c', type: 'dashed', width: 2 },
      itemStyle: { color: '#fb923c' },
    },
  ],
}))

const nutritionOption = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: { ...tooltip, valueFormatter: (value) => `${decimal.format(Number(value))} ${t('common.grams')}` },
  legend: {
    top: 0,
    right: 0,
    data: [t('charts.protein'), t('charts.carbs'), t('charts.fat')],
    textStyle: legendText,
  },
  grid: { left: 58, right: 18, top: 48, bottom: 38 },
  xAxis: {
    type: 'category',
    data: points.value.map((item) => formatGermanDayMonth(item.date)),
    axisLine,
    axisTick: { show: false },
    axisLabel: { ...axisLabel, hideOverlap: true },
  },
  yAxis: { type: 'value', name: t('common.grams'), nameTextStyle: axisLabel, axisLabel, splitLine },
  series: [
    {
      name: t('charts.protein'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.protein_g),
      lineStyle: { color: '#2dd4bf', width: 2 },
    },
    {
      name: t('charts.carbs'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.carbs_g),
      lineStyle: { color: '#f6ad35', width: 2 },
    },
    {
      name: t('charts.fat'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.fat_g),
      lineStyle: { color: '#a78bfa', width: 2 },
    },
  ],
}))
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('trends.title') }}</h1><p>{{ t('trends.description') }}</p></div>
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('trends.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('trends.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhTrendUp :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.average7') }}</small><strong>{{ latestSevenDayAverage == null ? '–' : `${integer.format(latestSevenDayAverage)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.latestDay') }}</small><strong>{{ latestNutrition?.calories_kcal == null ? '–' : `${integer.format(latestNutrition.calories_kcal)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhBarbell :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.latestProtein') }}</small><strong>{{ latestNutrition?.protein_g == null ? '–' : `${decimal.format(latestNutrition.protein_g)} g` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.dataBasis') }}</small><strong>{{ t('trends.recordedDays', { recorded: recordedDays, total: points.length }) }}</strong></span>
      </article>
    </section>
    <div class="trend-chart-grid">
      <ChartPanel
        class="trend-chart-wide"
        :title="t('trends.caloriesChart')"
        :option="calorieOption"
        :empty="!points.some((item) => item.calories_kcal != null)"
        :height="350"
      >
        <template #header-actions><span class="chart-range">{{ rangeLabel }}</span></template>
      </ChartPanel>
      <ChartPanel
        class="trend-chart-wide"
        :title="t('trends.macroChart')"
        :option="nutritionOption"
        :empty="!points.some((item) => item.protein_g != null || item.carbs_g != null || item.fat_g != null)"
        :height="310"
      />
    </div>
  </template>
</template>
