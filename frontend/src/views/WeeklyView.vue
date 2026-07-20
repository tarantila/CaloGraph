<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ChartPanel from '../components/ChartPanel.vue'

interface Week { week_start: string; consumed_kcal: number; budget_kcal: number; deviation_kcal: number; remaining_kcal: number; mean_kcal: number | null; median_kcal: number | null }
const weeks = ref<Week[]>([])
const error = ref('')
const format = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
onMounted(async () => {
  try { weeks.value = (await api<{ weeks: Week[] }>('/analytics/weekly')).weeks } catch { error.value = 'Wochenbudgets konnten nicht geladen werden.' }
})
const option = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' }, legend: { data: ['Aufnahme', 'Budget'] },
  xAxis: { type: 'category', data: weeks.value.map((week) => new Date(`${week.week_start}T12:00:00`).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' })) },
  yAxis: { type: 'value', name: 'kcal' },
  series: [
    { name: 'Aufnahme', type: 'bar', data: weeks.value.map((week) => week.consumed_kcal), itemStyle: { color: '#72a996' } },
    { name: 'Budget', type: 'line', data: weeks.value.map((week) => week.budget_kcal), lineStyle: { color: '#8a6215', width: 3 } },
  ],
}))
</script>

<template>
  <div class="page-heading"><div><h1>Wochenbudget</h1><p>Einzelne Tage werden im Zusammenhang der ganzen Woche bewertet.</p></div></div>
  <div v-if="error" class="card error">{{ error }}</div>
  <template v-else>
    <ChartPanel title="Aufnahme im Vergleich zum Wochenbudget" :option="option" :empty="!weeks.length" />
    <section class="card table-card"><div class="table-scroll"><table>
      <thead><tr><th>Woche ab</th><th class="number">Aufnahme</th><th class="number">Budget</th><th class="number">Abweichung</th><th class="number">Tagesmittel</th><th class="number">Median</th></tr></thead>
      <tbody><tr v-for="week in weeks" :key="week.week_start"><td>{{ new Date(`${week.week_start}T12:00:00`).toLocaleDateString('de-DE') }}</td><td class="number">{{ format.format(week.consumed_kcal) }} kcal</td><td class="number">{{ format.format(week.budget_kcal) }} kcal</td><td class="number">{{ week.deviation_kcal >= 0 ? '+' : '' }}{{ format.format(week.deviation_kcal) }} kcal</td><td class="number">{{ week.mean_kcal == null ? '–' : `${format.format(week.mean_kcal)} kcal` }}</td><td class="number">{{ week.median_kcal == null ? '–' : `${format.format(week.median_kcal)} kcal` }}</td></tr></tbody>
    </table></div></section>
  </template>
</template>

