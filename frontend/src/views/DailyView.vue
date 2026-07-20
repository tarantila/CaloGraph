<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api'
import DateFilter from '../components/DateFilter.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'

const route = useRoute()
const router = useRouter()
const today = new Date().toISOString().slice(0, 10)
const before = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10)
const start = ref(String(route.query.start ?? before))
const end = ref(String(route.query.end ?? today))
const source = ref(String(route.query.source ?? ''))
const tracking = ref(String(route.query.tracking ?? ''))
const weekday = ref(String(route.query.weekday ?? ''))
const points = ref<DailyPoint[]>([])
const error = ref('')
const auth = useAuthStore()
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

async function load() {
  error.value = ''
  await router.replace({ query: { start: start.value, end: end.value, source: source.value || undefined, tracking: tracking.value || undefined, weekday: weekday.value || undefined } })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (source.value) params.set('source', source.value)
    if (tracking.value) params.set('tracking', tracking.value)
    if (weekday.value) params.set('weekday', weekday.value)
    points.value = await api<DailyPoint[]>(`/analytics/daily?${params}`)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Tageswerte konnten nicht geladen werden.'
  }
}
onMounted(load)
const display = (value: number | null, unit = '') => (value == null ? '–' : `${number.format(value)}${unit}`)
const displayWeight = (value: number | null) => value == null ? '–' : `${number.format(auth.user?.preferred_weight_unit === 'lb' ? value * 2.2046226218 : value)} ${auth.user?.preferred_weight_unit ?? 'kg'}`
</script>

<template>
  <div class="page-heading">
    <div><h1>Tagesverlauf</h1><p>Aufnahme, Aktivität und Vollständigkeit pro Tag.</p></div>
    <DateFilter v-model:start="start" v-model:end="end" @apply="load" />
  </div>
  <div class="filters" style="margin-bottom: 1rem">
    <label class="field">Datenquelle<input v-model="source" placeholder="z. B. health_auto_export_v2" /></label>
    <label class="field">Vollständigkeit<select v-model="tracking"><option value="">Alle</option><option value="complete,probably_complete">Vollständige Tage</option><option value="probably_incomplete,incomplete">Unvollständige Tage</option><option value="no_data">Keine Daten</option></select></label>
    <label class="field">Wochentag<select v-model="weekday"><option value="">Alle</option><option v-for="(label, index) in ['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag']" :key="label" :value="String(index)">{{ label }}</option></select></label>
    <button class="button secondary" type="button" @click="load">Filter anwenden</button>
  </div>
  <div v-if="error" class="card error">{{ error }}</div>
  <section v-else class="card table-card">
    <div class="table-scroll">
      <table>
        <thead><tr><th>Datum</th><th>Status</th><th class="number">Kalorien</th><th class="number">Abweichung</th><th class="number">Eiweiß</th><th class="number">Kohlenhydrate</th><th class="number">Fett</th><th class="number">Aktiv</th><th class="number">Gewicht</th></tr></thead>
        <tbody>
          <tr v-for="point in points" :key="point.date">
            <td>{{ new Date(`${point.date}T12:00:00`).toLocaleDateString('de-DE') }}</td>
            <td><StatusBadge :status="point.tracking_status" /></td>
            <td class="number">{{ display(point.calories_kcal, ' kcal') }}</td>
            <td class="number">{{ display(point.deviation_kcal, ' kcal') }}</td>
            <td class="number">{{ display(point.protein_g, ' g') }}</td>
            <td class="number">{{ display(point.carbs_g, ' g') }}</td>
            <td class="number">{{ display(point.fat_g, ' g') }}</td>
            <td class="number">{{ display(point.active_energy_kcal, ' kcal') }}</td>
            <td class="number">{{ displayWeight(point.weight_kg) }}</td>
          </tr>
          <tr v-if="!points.length"><td colspan="9" class="empty">Keine Tage im gewählten Zeitraum.</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
