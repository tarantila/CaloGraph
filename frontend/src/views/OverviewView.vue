<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import StatCard from '../components/StatCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'

interface Summary {
  today: DailyPoint
  week: { consumed_kcal: number; budget_kcal: number; deviation_kcal: number; remaining_kcal: number }
  protein_7d_average_g: number | null
  current_weight_kg: number | null
  weight_change_kg: number | null
  last_import_at: string | null
}

const summary = ref<Summary | null>(null)
const trends = ref<DailyPoint[]>([])
const error = ref('')
const auth = useAuthStore()
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const weightUnit = computed(() => auth.user?.preferred_weight_unit ?? 'kg')
const displayWeight = (value: number) => weightUnit.value === 'lb' ? value * 2.2046226218 : value

onMounted(async () => {
  try {
    const [summaryResult, trendResult] = await Promise.all([
      api<Summary>('/dashboard/summary'),
      api<{ points: DailyPoint[] }>('/analytics/trends'),
    ])
    summary.value = summaryResult
    trends.value = trendResult.points
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Dashboard konnte nicht geladen werden.'
  }
})

const chartOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: ['Kalorien', '7-Tage-Mittel', 'Ziel'], textStyle: { color: '#607069' } },
  grid: { left: 48, right: 18, top: 45, bottom: 38 },
  xAxis: { type: 'category', data: trends.value.map((item) => item.date.slice(5)) },
  yAxis: { type: 'value', name: 'kcal' },
  series: [
    { name: 'Kalorien', type: 'bar', data: trends.value.map((item) => item.calories_kcal), itemStyle: { color: '#85b7a7' } },
    { name: '7-Tage-Mittel', type: 'line', data: trends.value.map((item) => item.average_7d), smooth: true, showSymbol: false, lineStyle: { color: '#167a62', width: 3 } },
    { name: 'Ziel', type: 'line', data: trends.value.map((item) => item.target_kcal), showSymbol: false, lineStyle: { color: '#8a6215', type: 'dashed' } },
  ],
}))
</script>

<template>
  <div class="page-heading">
    <div><h1>Übersicht</h1><p>Tageswerte im Kontext deines Wochenbudgets.</p></div>
    <StatusBadge v-if="summary" :status="summary.today.tracking_status" />
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <template v-else-if="summary">
    <section class="stats-grid" aria-label="Kennzahlen">
      <StatCard label="Kalorien heute" :value="summary.today.calories_kcal == null ? '–' : `${number.format(summary.today.calories_kcal)} kcal`" :hint="summary.today.target_kcal ? `Ziel ${number.format(summary.today.target_kcal)} kcal` : 'Kein Ziel hinterlegt'" />
      <StatCard label="Wochenbudget verbleibend" :value="`${number.format(summary.week.remaining_kcal)} kcal`" :hint="`${number.format(summary.week.consumed_kcal)} von ${number.format(summary.week.budget_kcal)} kcal`" />
      <StatCard label="Eiweiß heute" :value="summary.today.protein_g == null ? '–' : `${decimal.format(summary.today.protein_g)} g`" :hint="summary.protein_7d_average_g == null ? 'Kein 7-Tage-Mittel' : `Ø 7 Tage: ${decimal.format(summary.protein_7d_average_g)} g`" />
      <StatCard label="Aktuelles Gewicht" :value="summary.current_weight_kg == null ? '–' : `${decimal.format(displayWeight(summary.current_weight_kg))} ${weightUnit}`" :hint="summary.weight_change_kg == null ? 'Keine Vergleichsbasis' : `${summary.weight_change_kg >= 0 ? '+' : ''}${decimal.format(displayWeight(summary.weight_change_kg))} ${weightUnit} zur Vorwoche`" />
    </section>
    <div class="content-grid">
      <ChartPanel title="Kalorien und gleitender Mittelwert" :option="chartOption" :empty="!trends.some((item) => item.calories_kcal != null)" />
      <section class="card chart-card">
        <h2>Datenqualität heute</h2>
        <StatusBadge :status="summary.today.tracking_status" />
        <ul>
          <li v-for="reason in summary.today.tracking_reasons" :key="reason">{{ reason }}</li>
        </ul>
        <p class="hint">Letzter Import: {{ summary.last_import_at ? new Date(summary.last_import_at).toLocaleString('de-DE') : 'noch keiner' }}</p>
      </section>
    </div>
  </template>
  <div v-else class="card empty">Dashboard wird geladen …</div>
</template>
