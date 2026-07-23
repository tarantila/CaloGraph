<script setup lang="ts">
import {
  PhBarbell,
  PhCalendarBlank,
  PhChartBar,
  PhTrendUp,
} from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import type { DailyPoint } from '../types'

const points = ref<DailyPoint[]>([])
const loading = ref(true)
const error = ref('')
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

async function load() {
  loading.value = true
  error.value = ''
  try {
    points.value = (await api<{ points: DailyPoint[] }>('/analytics/trends')).points
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Trenddaten konnten nicht geladen werden.'
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
  if (!points.value.length) return 'Keine Daten'
  const first = new Date(`${points.value[0].date}T12:00:00`).toLocaleDateString('de-DE', {
    day: '2-digit',
    month: 'short',
  })
  const last = new Date(`${points.value.at(-1)!.date}T12:00:00`).toLocaleDateString('de-DE', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
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
  tooltip: { ...tooltip, valueFormatter: (value) => `${integer.format(Number(value))} kcal` },
  legend: {
    top: 0,
    right: 0,
    data: ['Kalorien', '7 Tage', '14 Tage', '28 Tage', 'Tagesbudget'],
    textStyle: legendText,
    itemWidth: 14,
    itemHeight: 8,
  },
  grid: { left: 58, right: 18, top: 54, bottom: 38 },
  xAxis: {
    type: 'category',
    data: points.value.map((item) => item.date.slice(5)),
    axisLine,
    axisTick: { show: false },
    axisLabel: { ...axisLabel, hideOverlap: true },
  },
  yAxis: {
    type: 'value',
    name: 'kcal',
    nameTextStyle: axisLabel,
    axisLabel,
    splitLine,
  },
  series: [
    {
      name: 'Kalorien',
      type: 'bar',
      barMaxWidth: 24,
      data: points.value.map((item) => item.calories_kcal),
      itemStyle: { color: '#8b5cf6', borderRadius: [4, 4, 0, 0] },
    },
    {
      name: '7 Tage',
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_7d),
      lineStyle: { color: '#2dd4bf', width: 3 },
    },
    {
      name: '14 Tage',
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_14d),
      lineStyle: { color: '#5b8ff9', width: 2 },
    },
    {
      name: '28 Tage',
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.average_28d),
      lineStyle: { color: '#d19bff', width: 2 },
    },
    {
      name: 'Tagesbudget',
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
  tooltip: { ...tooltip, valueFormatter: (value) => `${decimal.format(Number(value))} g` },
  legend: {
    top: 0,
    right: 0,
    data: ['Protein', 'Kohlenhydrate', 'Fett'],
    textStyle: legendText,
  },
  grid: { left: 58, right: 18, top: 48, bottom: 38 },
  xAxis: {
    type: 'category',
    data: points.value.map((item) => item.date.slice(5)),
    axisLine,
    axisTick: { show: false },
    axisLabel: { ...axisLabel, hideOverlap: true },
  },
  yAxis: { type: 'value', name: 'g', nameTextStyle: axisLabel, axisLabel, splitLine },
  series: [
    {
      name: 'Protein',
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.protein_g),
      lineStyle: { color: '#2dd4bf', width: 2 },
    },
    {
      name: 'Kohlenhydrate',
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.carbs_g),
      lineStyle: { color: '#f6ad35', width: 2 },
    },
    {
      name: 'Fett',
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
    <div><h1>Trends</h1><p>Gleitende Mittelwerte zeigen die Entwicklung, ohne Datenlücken als null zu behandeln.</p></div>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Trends werden berechnet …</div>
  <template v-else>
    <section class="insight-strip" aria-label="Trendkennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhTrendUp :size="20" weight="duotone" /></span>
        <span><small>7-Tage-Schnitt</small><strong>{{ latestSevenDayAverage == null ? '–' : `${integer.format(latestSevenDayAverage)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>Letzter erfasster Tag</small><strong>{{ latestNutrition?.calories_kcal == null ? '–' : `${integer.format(latestNutrition.calories_kcal)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhBarbell :size="20" weight="duotone" /></span>
        <span><small>Protein am letzten Tag</small><strong>{{ latestNutrition?.protein_g == null ? '–' : `${decimal.format(latestNutrition.protein_g)} g` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Datenbasis</small><strong>{{ recordedDays }} von {{ points.length }} Tagen</strong></span>
      </article>
    </section>

    <div class="trend-chart-grid">
      <ChartPanel
        class="trend-chart-wide"
        title="Kalorien und gleitende Mittelwerte"
        :option="calorieOption"
        :empty="!points.some((item) => item.calories_kcal != null)"
        :height="350"
      >
        <template #header-actions><span class="chart-range">{{ rangeLabel }}</span></template>
      </ChartPanel>
      <ChartPanel
        class="trend-chart-wide"
        title="Makronährstoffe"
        :option="nutritionOption"
        :empty="!points.some((item) => item.protein_g != null || item.carbs_g != null || item.fat_g != null)"
        :height="310"
      />
    </div>
  </template>
</template>
