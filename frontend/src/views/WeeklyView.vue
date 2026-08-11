<script setup lang="ts">
import {
  PhCalendarBlank,
  PhChartBar,
  PhClockCounterClockwise,
  PhGauge,
} from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, formatGermanDayMonth, shiftIsoDate } from '../date-format'

interface Week {
  week_start: string
  consumed_kcal: number
  budget_kcal: number | null
  deviation_kcal: number | null
  remaining_kcal: number | null
  mean_kcal: number | null
  median_kcal: number | null
}

const weeks = ref<Week[]>([])
const error = ref('')
const loading = ref(true)
const format = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })

onMounted(async () => {
  try {
    weeks.value = (await api<{ weeks: Week[] }>('/analytics/weekly')).weeks
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Wochenbudgets konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
})

const latestWeek = computed(() => weeks.value.at(-1) ?? null)
const recordedWeeks = computed(() =>
  weeks.value.filter((week) => week.mean_kcal != null),
)
const averageWeek = computed(() =>
  recordedWeeks.value.length
    ? recordedWeeks.value.reduce((sum, week) => sum + week.consumed_kcal, 0) /
      recordedWeeks.value.length
    : null,
)
const weeksWithinBudget = computed(() =>
  recordedWeeks.value.filter(
    (week) => week.deviation_kcal != null && week.deviation_kcal <= 0,
  ).length,
)

const option = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#111d30',
    borderColor: '#324157',
    textStyle: { color: '#f3f6fb' },
    valueFormatter: (value) => `${format.format(Number(value))} kcal`,
  },
  legend: {
    top: 0,
    right: 0,
    data: ['Aufnahme', 'Wochenbudget'],
    textStyle: { color: '#98a5b9', fontFamily: 'Inter' },
  },
  grid: { left: 58, right: 18, top: 48, bottom: 40 },
  xAxis: {
    type: 'category',
    data: weeks.value.map((week) => formatGermanDayMonth(week.week_start)),
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
      name: 'Aufnahme',
      type: 'bar',
      barMaxWidth: 34,
      data: weeks.value.map((week) => week.consumed_kcal),
      itemStyle: { color: '#8b5cf6', borderRadius: [5, 5, 0, 0] },
    },
    {
      name: 'Wochenbudget',
      type: 'line',
      showSymbol: false,
      data: weeks.value.map((week) => week.budget_kcal),
      lineStyle: { color: '#fb923c', width: 2, type: 'dashed' },
      itemStyle: { color: '#fb923c' },
    },
  ],
}))

function weekLabel(value: string) {
  return `${formatGermanDayMonth(value)} – ${formatGermanDate(shiftIsoDate(value, 6))}`
}
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>Wochenbudget</h1>
      <p>Kalorienaufnahme im Zusammenhang einer vollständigen Woche von Montag bis Sonntag.</p>
    </div>
    <span class="page-context">Letzte {{ weeks.length }} Wochen</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading" aria-live="polite">
    Wochen werden geladen …
  </div>
  <template v-else>
    <section class="insight-strip" aria-label="Wochenkennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>Aktuelle Woche</small><strong>{{ latestWeek ? `${format.format(latestWeek.consumed_kcal)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhGauge :size="20" weight="duotone" /></span>
        <span><small>Noch verfügbar</small><strong>{{ latestWeek?.remaining_kcal == null ? '–' : `${format.format(Math.max(latestWeek.remaining_kcal, 0))} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhClockCounterClockwise :size="20" weight="duotone" /></span>
        <span><small>Ø pro Woche</small><strong>{{ averageWeek == null ? '–' : `${format.format(averageWeek)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Im Budget</small><strong>{{ weeksWithinBudget }} von {{ recordedWeeks.length }}</strong></span>
      </article>
    </section>

    <ChartPanel
      title="Aufnahme und Wochenbudget"
      :option="option"
      :empty="!recordedWeeks.length"
      :height="330"
    >
      <template #header-actions>
        <span class="chart-range">Montag bis Sonntag</span>
      </template>
    </ChartPanel>

    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>Wochen im Detail</h2><p>Budgetwerte berücksichtigen die jeweils gültige Zielhistorie.</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr><th>Woche</th><th class="number">Aufnahme</th><th class="number">Budget</th><th class="number">Differenz</th><th class="number">Tagesmittel</th><th class="number">Median</th></tr>
          </thead>
          <tbody>
            <tr v-for="week in [...weeks].reverse()" :key="week.week_start">
              <td><strong>{{ weekLabel(week.week_start) }}</strong></td>
              <td class="number">{{ format.format(week.consumed_kcal) }} kcal</td>
              <td class="number">{{ week.budget_kcal == null ? '–' : `${format.format(week.budget_kcal)} kcal` }}</td>
              <td :class="['number', 'difference-value', week.deviation_kcal == null ? null : week.deviation_kcal > 0 ? 'over' : 'under']">
                <template v-if="week.deviation_kcal != null">{{ week.deviation_kcal > 0 ? '+' : '' }}{{ format.format(week.deviation_kcal) }} kcal</template>
                <template v-else>–</template>
              </td>
              <td class="number">{{ week.mean_kcal == null ? '–' : `${format.format(week.mean_kcal)} kcal` }}</td>
              <td class="number">{{ week.median_kcal == null ? '–' : `${format.format(week.median_kcal)} kcal` }}</td>
            </tr>
            <tr v-if="!weeks.length"><td colspan="6" class="empty">Noch keine Wochenwerte vorhanden.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
