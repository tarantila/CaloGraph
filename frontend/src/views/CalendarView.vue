<script setup lang="ts">
import {
  PhCalendarBlank,
  PhCheckCircle,
  PhFire,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import type { DailyPoint } from '../types'

const days = ref<DailyPoint[]>([])
const error = ref('')
const loading = ref(true)
const labels: Record<string, string> = {
  well_below: 'Deutlich unter Budget',
  slightly_below: 'Unter Budget',
  on_target: 'Budget nahezu ausgeschöpft',
  slightly_above: 'Leicht über Budget',
  well_above: 'Deutlich über Budget',
  probably_incomplete: 'Kalorienwert fehlt',
  no_data: 'Keine Daten',
}
const format = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })

onMounted(async () => {
  try {
    days.value = (await api<{ days: DailyPoint[] }>('/analytics/calendar')).days
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Kalender konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
})

const recordedDays = computed(() => days.value.filter((day) => day.calories_kcal != null))
const withinBudgetDays = computed(() =>
  days.value.filter((day) =>
    ['well_below', 'slightly_below', 'on_target'].includes(day.classification ?? ''),
  ),
)
const missingDays = computed(() => days.value.filter((day) => day.classification === 'no_data'))
const averageCalories = computed(() =>
  recordedDays.value.length
    ? recordedDays.value.reduce((sum, day) => sum + (day.calories_kcal ?? 0), 0) /
      recordedDays.value.length
    : null,
)
const rangeLabel = computed(() => {
  if (!days.value.length) return 'Keine Kalendertage'
  const formatDate = (value: string, withYear = false) =>
    new Date(`${value}T12:00:00`).toLocaleDateString('de-DE', {
      day: '2-digit',
      month: 'short',
      year: withYear ? 'numeric' : undefined,
    })
  return `${formatDate(days.value[0].date)} – ${formatDate(days.value.at(-1)!.date, true)}`
})
</script>

<template>
  <div class="page-heading">
    <div><h1>Kalender</h1><p>Kalorienbudget und Datenabdeckung Tag für Tag – ohne moralische Bewertung.</p></div>
    <span class="page-context">{{ rangeLabel }}</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Kalender wird geladen …</div>
  <template v-else>
    <section class="insight-strip" aria-label="Kalenderkennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Erfasste Tage</small><strong>{{ recordedDays.length }} von {{ days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhFire :size="20" weight="duotone" /></span>
        <span><small>Ø Kalorien</small><strong>{{ averageCalories == null ? '–' : `${format.format(averageCalories)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>Bis zum Budget</small><strong>{{ withinBudgetDays.length }} Tage</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>Ohne Daten</small><strong>{{ missingDays.length }} Tage</strong></span>
      </article>
    </section>

    <section class="card calendar-card">
      <div class="section-card-header calendar-card-header">
        <div><h2>Tagesübersicht</h2><p>Orange markiert Überschreitungen, Blau verbleibendes Budget und Grün einen nahezu ausgeschöpften Rahmen.</p></div>
        <div class="calendar-legend" aria-label="Kalenderlegende">
          <span><i class="under"></i>Unter Budget</span>
          <span><i class="near"></i>Nahe Budget</span>
          <span><i class="over"></i>Über Budget</span>
          <span><i class="missing"></i>Keine Daten</span>
        </div>
      </div>
      <div class="calendar-grid">
        <article
          v-for="day in days"
          :key="day.date"
          :class="['calendar-day', day.classification]"
          :aria-label="`${day.date}: ${labels[day.classification ?? 'no_data']}`"
        >
          <div class="calendar-day-heading">
            <strong>{{ new Date(`${day.date}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'short' }) }}</strong>
            <time :datetime="day.date">{{ new Date(`${day.date}T12:00:00`).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' }) }}</time>
          </div>
          <b>{{ day.calories_kcal == null ? '–' : format.format(day.calories_kcal) }}<small v-if="day.calories_kcal != null"> kcal</small></b>
          <span>{{ labels[day.classification ?? 'no_data'] }}</span>
          <StatusBadge v-if="day.tracking_status !== 'complete' && day.tracking_status !== 'no_data'" :status="day.tracking_status" />
        </article>
        <div v-if="!days.length" class="empty">Für diesen Zeitraum liegen keine Kalendertage vor.</div>
      </div>
    </section>
  </template>
</template>
