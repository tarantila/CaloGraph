<script setup lang="ts">
import {
  PhBarbell,
  PhCalendarBlank,
  PhChartBar,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'

interface Weekday {
  weekday: number
  label: string
  count: number
  mean_kcal: number | null
  median_kcal: number | null
  p25_kcal: number | null
  p75_kcal: number | null
  mean_deviation_kcal: number | null
  mean_protein_g: number | null
}

const weekdays = ref<Weekday[]>([])
const error = ref('')
const loading = ref(true)
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })

onMounted(async () => {
  try {
    weekdays.value = (await api<{ weekdays: Weekday[] }>('/analytics/weekdays')).weekdays
  } catch (cause) {
    error.value =
      cause instanceof ApiError
        ? cause.message
        : 'Wochentagsanalyse konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
})

const recorded = computed(() => weekdays.value.filter((item) => item.count > 0))
const highestDay = computed(() =>
  [...recorded.value].sort((a, b) => (b.mean_kcal ?? 0) - (a.mean_kcal ?? 0)).at(0) ?? null,
)
const lowestDay = computed(() =>
  [...recorded.value].sort((a, b) => (a.mean_kcal ?? 0) - (b.mean_kcal ?? 0)).at(0) ?? null,
)
const proteinDay = computed(() =>
  [...recorded.value].sort((a, b) => (b.mean_protein_g ?? 0) - (a.mean_protein_g ?? 0)).at(0) ??
  null,
)
const totalDays = computed(() => recorded.value.reduce((sum, item) => sum + item.count, 0))

const option = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#111d30',
    borderColor: '#324157',
    textStyle: { color: '#f3f6fb' },
    valueFormatter: (value) => `${number.format(Number(value))} kcal`,
  },
  legend: {
    top: 0,
    right: 0,
    data: ['Mittelwert', 'Median'],
    textStyle: { color: '#98a5b9', fontFamily: 'Inter' },
  },
  grid: { left: 58, right: 18, top: 48, bottom: 38 },
  xAxis: {
    type: 'category',
    data: weekdays.value.map((item) => item.label.slice(0, 2)),
    axisLine: { lineStyle: { color: '#263449' } },
    axisTick: { show: false },
    axisLabel: { color: '#98a5b9', fontFamily: 'Inter' },
  },
  yAxis: {
    type: 'value',
    name: 'kcal',
    nameTextStyle: { color: '#98a5b9', fontFamily: 'Inter' },
    axisLabel: { color: '#98a5b9', fontFamily: 'Inter' },
    splitLine: { lineStyle: { color: '#263449', type: 'dashed' } },
  },
  series: [
    {
      name: 'Mittelwert',
      type: 'bar',
      barMaxWidth: 42,
      data: weekdays.value.map((item) => item.mean_kcal),
      itemStyle: { color: '#8b5cf6', borderRadius: [5, 5, 0, 0] },
    },
    {
      name: 'Median',
      type: 'line',
      showSymbol: true,
      symbolSize: 7,
      data: weekdays.value.map((item) => item.median_kcal),
      lineStyle: { color: '#2dd4bf', width: 2 },
      itemStyle: { color: '#2dd4bf' },
    },
  ],
}))
</script>

<template>
  <div class="page-heading">
    <div><h1>Wochentagsanalyse</h1><p>Wiederkehrende Muster von Montag bis Sonntag auf Basis erfasster Tage.</p></div>
    <span class="page-context">{{ totalDays }} ausgewertete Tage</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Wochentage werden ausgewertet …</div>
  <template v-else>
    <section class="insight-strip" aria-label="Wochentagskennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>Höchster Schnitt</small><strong>{{ highestDay ? `${highestDay.label} · ${number.format(highestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Niedrigster Schnitt</small><strong>{{ lowestDay ? `${lowestDay.label} · ${number.format(lowestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhBarbell :size="20" weight="duotone" /></span>
        <span><small>Meistes Protein</small><strong>{{ proteinDay?.mean_protein_g == null ? '–' : `${proteinDay.label} · ${number.format(proteinDay.mean_protein_g)} g` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>Datenbasis</small><strong>{{ totalDays }} Tage</strong></span>
      </article>
    </section>

    <ChartPanel
      title="Kalorien nach Wochentag"
      :option="option"
      :empty="!recorded.length"
      :height="330"
    >
      <template #header-actions><span class="chart-range">Mittelwert und Median</span></template>
    </ChartPanel>

    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>Verteilung je Wochentag</h2><p>Das Intervall zeigt den Bereich zwischen dem 25. und 75. Perzentil.</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Tag</th><th class="number">Tage</th><th class="number">Mittel</th><th class="number">Median</th><th class="number">Mittlere 50 %</th><th class="number">Ø Budgetdifferenz</th><th class="number">Ø Protein</th></tr></thead>
          <tbody>
            <tr v-for="item in weekdays" :key="item.weekday">
              <td><strong>{{ item.label }}</strong></td>
              <td class="number">{{ item.count }}</td>
              <td class="number">{{ item.mean_kcal == null ? '–' : `${number.format(item.mean_kcal)} kcal` }}</td>
              <td class="number">{{ item.median_kcal == null ? '–' : `${number.format(item.median_kcal)} kcal` }}</td>
              <td class="number">{{ item.p25_kcal == null ? '–' : `${number.format(item.p25_kcal)}–${number.format(item.p75_kcal ?? 0)} kcal` }}</td>
              <td :class="['number', 'difference-value', (item.mean_deviation_kcal ?? 0) > 0 ? 'over' : 'under']">{{ item.mean_deviation_kcal == null ? '–' : `${item.mean_deviation_kcal > 0 ? '+' : ''}${number.format(item.mean_deviation_kcal)} kcal` }}</td>
              <td class="number">{{ item.mean_protein_g == null ? '–' : `${number.format(item.mean_protein_g)} g` }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
