<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'

const points = ref<DailyPoint[]>([])
const includeIncomplete = ref(false)
const auth = useAuthStore()
const weightFactor = computed(() => auth.user?.preferred_weight_unit === 'lb' ? 2.2046226218 : 1)
const weightUnit = computed(() => auth.user?.preferred_weight_unit ?? 'kg')
async function load() { points.value = (await api<{ points: DailyPoint[] }>(`/analytics/trends?include_incomplete=${includeIncomplete.value}`)).points }
onMounted(load)
const calorieOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' }, legend: { data: ['Kalorien', '7 Tage', '14 Tage', '28 Tage', 'Ziel'] },
  xAxis: { type: 'category', data: points.value.map((item) => item.date.slice(5)) }, yAxis: { type: 'value', name: 'kcal' },
  series: [
    { name: 'Kalorien', type: 'bar', data: points.value.map((item) => item.calories_kcal), itemStyle: { color: '#bad5cc' } },
    { name: '7 Tage', type: 'line', showSymbol: false, data: points.value.map((item) => item.average_7d), lineStyle: { color: '#167a62', width: 3 } },
    { name: '14 Tage', type: 'line', showSymbol: false, data: points.value.map((item) => item.average_14d), lineStyle: { color: '#527d9b' } },
    { name: '28 Tage', type: 'line', showSymbol: false, data: points.value.map((item) => item.average_28d), lineStyle: { color: '#7b6899' } },
    { name: 'Ziel', type: 'line', showSymbol: false, data: points.value.map((item) => item.target_kcal), lineStyle: { color: '#8a6215', type: 'dashed' } },
  ],
}))
const weightOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' }, legend: { data: ['Gewicht', '7-Tage-Mittel'] }, xAxis: { type: 'category', data: points.value.map((item) => item.date.slice(5)) }, yAxis: { type: 'value', name: weightUnit.value, scale: true },
  series: [
    { name: 'Gewicht', type: 'line', connectNulls: false, data: points.value.map((item) => item.weight_kg == null ? null : item.weight_kg * weightFactor.value), lineStyle: { color: '#8eb1c7' }, symbolSize: 4 },
    { name: '7-Tage-Mittel', type: 'line', connectNulls: false, showSymbol: false, data: points.value.map((item) => item.weight_average_7d == null ? null : item.weight_average_7d * weightFactor.value), lineStyle: { color: '#315f7d', width: 3 } },
  ],
}))
const nutritionOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' }, legend: { data: ['Eiweiß', 'Aktive Energie'] },
  xAxis: { type: 'category', data: points.value.map((item) => item.date.slice(5)) },
  yAxis: [{ type: 'value', name: 'g' }, { type: 'value', name: 'kcal' }],
  series: [
    { name: 'Eiweiß', type: 'line', showSymbol: false, data: points.value.map((item) => item.protein_g), lineStyle: { color: '#7b6899', width: 2 } },
    { name: 'Aktive Energie', type: 'bar', yAxisIndex: 1, data: points.value.map((item) => item.active_energy_kcal), itemStyle: { color: '#bad5cc' } },
  ],
}))
</script>

<template>
  <div class="page-heading"><div><h1>Trends</h1><p>Gleitende Mittelwerte behandeln Datenlücken als fehlend, nicht als null.</p></div><label class="field">Tracking-Tage<select v-model="includeIncomplete" @change="load"><option :value="false">Nur plausible Tage</option><option :value="true">Alle Tage</option></select></label></div>
  <ChartPanel title="Kalorien und gleitende Mittelwerte" :option="calorieOption" :empty="!points.some((item) => item.calories_kcal != null)" />
  <div style="height: 1rem"></div>
  <ChartPanel title="Gewichtsverlauf" :option="weightOption" :empty="!points.some((item) => item.weight_kg != null)" />
  <div style="height: 1rem"></div>
  <ChartPanel title="Eiweiß und aktive Energie" :option="nutritionOption" :empty="!points.some((item) => item.protein_g != null || item.active_energy_kcal != null)" />
</template>
