<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ChartPanel from '../components/ChartPanel.vue'

interface Weekday { weekday: number; label: string; count: number; mean_kcal: number | null; median_kcal: number | null; p25_kcal: number | null; p75_kcal: number | null; mean_deviation_kcal: number | null; mean_protein_g: number | null; incomplete_share: number | null }
const weekdays = ref<Weekday[]>([])
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
onMounted(async () => { weekdays.value = (await api<{ weekdays: Weekday[] }>('/analytics/weekdays')).weekdays })
const option = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' }, legend: { data: ['Mittelwert', 'Median'] },
  xAxis: { type: 'category', data: weekdays.value.map((item) => item.label.slice(0, 2)) }, yAxis: { type: 'value', name: 'kcal' },
  series: [
    { name: 'Mittelwert', type: 'bar', data: weekdays.value.map((item) => item.mean_kcal), itemStyle: { color: '#72a996' } },
    { name: 'Median', type: 'line', data: weekdays.value.map((item) => item.median_kcal), lineStyle: { color: '#234f42', width: 3 } },
  ],
}))
</script>

<template>
  <div class="page-heading"><div><h1>Wochentagsanalyse</h1><p>Wiederkehrende Muster von Montag bis Sonntag.</p></div></div>
  <ChartPanel title="Kalorien nach Wochentag" :option="option" :empty="!weekdays.some((item) => item.count)" />
  <section class="card table-card"><div class="table-scroll"><table>
    <thead><tr><th>Tag</th><th class="number">Tage</th><th class="number">Mittel</th><th class="number">Median</th><th class="number">25.–75. Perzentil</th><th class="number">Ø Abweichung</th><th class="number">Ø Eiweiß</th><th class="number">Unvollständig</th></tr></thead>
    <tbody><tr v-for="item in weekdays" :key="item.weekday"><td>{{ item.label }}</td><td class="number">{{ item.count }}</td><td class="number">{{ item.mean_kcal == null ? '–' : number.format(item.mean_kcal) }}</td><td class="number">{{ item.median_kcal == null ? '–' : number.format(item.median_kcal) }}</td><td class="number">{{ item.p25_kcal == null ? '–' : `${number.format(item.p25_kcal)}–${number.format(item.p75_kcal ?? 0)}` }}</td><td class="number">{{ item.mean_deviation_kcal == null ? '–' : `${number.format(item.mean_deviation_kcal)} kcal` }}</td><td class="number">{{ item.mean_protein_g == null ? '–' : `${number.format(item.mean_protein_g)} g` }}</td><td class="number">{{ item.incomplete_share == null ? '–' : `${number.format(item.incomplete_share * 100)} %` }}</td></tr></tbody>
  </table></div></section>
</template>

