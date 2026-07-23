<script setup lang="ts">
import {
  PhCalendarBlank,
  PhCheckCircle,
  PhFire,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api'
import DateFilter from '../components/DateFilter.vue'
import StatusBadge from '../components/StatusBadge.vue'
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
const loading = ref(true)
const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

async function load() {
  error.value = ''
  loading.value = true
  await router.replace({ query: { start: start.value, end: end.value, source: source.value || undefined, tracking: tracking.value || undefined, weekday: weekday.value || undefined } })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (source.value) params.set('source', source.value)
    if (tracking.value) params.set('tracking', tracking.value)
    if (weekday.value) params.set('weekday', weekday.value)
    points.value = await api<DailyPoint[]>(`/analytics/daily?${params}`)
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Tageswerte konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
}
onMounted(load)
const display = (value: number | null, unit = '') => (value == null ? '–' : `${number.format(value)}${unit}`)
const recordedPoints = computed(() => points.value.filter((point) => point.calories_kcal != null))
const averageCalories = computed(() =>
  recordedPoints.value.length
    ? recordedPoints.value.reduce((sum, point) => sum + (point.calories_kcal ?? 0), 0) /
      recordedPoints.value.length
    : null,
)
const missingPoints = computed(() =>
  points.value.filter((point) => point.tracking_status === 'no_data'),
)
</script>

<template>
  <div class="page-heading">
    <div><h1>Tagesverlauf</h1><p>Aufnahme und Makronährstoffe für jeden Kalendertag.</p></div>
    <DateFilter v-model:start="start" v-model:end="end" @apply="load" />
  </div>
  <section class="card filter-panel" aria-label="Tageswerte filtern">
    <div class="filters">
      <label class="field">Datenquelle<input v-model="source" placeholder="Alle Quellen" /></label>
      <label class="field">Datenstatus<select v-model="tracking"><option value="">Alle</option><option value="complete,probably_complete">Mit Kalorienwert</option><option value="probably_incomplete,incomplete">Ohne Kalorienwert</option><option value="no_data">Keine Ernährungsdaten</option></select></label>
      <label class="field">Wochentag<select v-model="weekday"><option value="">Alle</option><option v-for="(label, index) in ['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag']" :key="label" :value="String(index)">{{ label }}</option></select></label>
      <button class="button secondary" type="button" @click="load">Filter anwenden</button>
    </div>
  </section>
  <div v-if="error" class="card error">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Tageswerte werden geladen …</div>
  <template v-else>
    <section class="insight-strip" aria-label="Tageskennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Tage im Zeitraum</small><strong>{{ points.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhFire :size="20" weight="duotone" /></span>
        <span><small>Ø Kalorien</small><strong>{{ averageCalories == null ? '–' : `${number.format(averageCalories)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>Mit Kalorienwert</small><strong>{{ recordedPoints.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>Ohne Daten</small><strong>{{ missingPoints.length }}</strong></span>
      </article>
    </section>
    <section class="card table-card daily-table">
      <div class="section-card-header">
        <div><h2>Einzelne Tage</h2><p>Fehlende Werte bleiben bewusst leer und werden nicht als null gerechnet.</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Datum</th><th>Status</th><th class="number">Kalorien</th><th class="number">Abweichung</th><th class="number">Eiweiß</th><th class="number">Kohlenhydrate</th><th class="number">Fett</th></tr></thead>
          <tbody>
            <tr v-for="point in points" :key="point.date">
              <td>{{ new Date(`${point.date}T12:00:00`).toLocaleDateString('de-DE') }}</td>
              <td><StatusBadge :status="point.tracking_status" /></td>
              <td class="number">{{ display(point.calories_kcal, ' kcal') }}</td>
              <td class="number">{{ display(point.deviation_kcal, ' kcal') }}</td>
              <td class="number">{{ display(point.protein_g, ' g') }}</td>
              <td class="number">{{ display(point.carbs_g, ' g') }}</td>
              <td class="number">{{ display(point.fat_g, ' g') }}</td>
            </tr>
            <tr v-if="!points.length"><td colspan="7" class="empty">Keine Tage im gewählten Zeitraum.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
