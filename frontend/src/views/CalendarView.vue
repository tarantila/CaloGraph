<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import type { DailyPoint } from '../types'

const days = ref<DailyPoint[]>([])
const labels: Record<string, string> = { well_below: 'Deutlich unter Ziel', slightly_below: 'Leicht unter Ziel', on_target: 'Im Zielbereich', slightly_above: 'Leicht über Ziel', well_above: 'Deutlich über Ziel', probably_incomplete: 'Wahrscheinlich unvollständig', no_data: 'Keine Daten' }
const format = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
onMounted(async () => { days.value = (await api<{ days: DailyPoint[] }>('/analytics/calendar')).days })
</script>

<template>
  <div class="page-heading"><div><h1>Kalender</h1><p>Abgestufte Zielabweichungen ohne moralische Bewertung.</p></div></div>
  <div class="calendar-grid">
    <article v-for="day in days" :key="day.date" :class="['calendar-day', day.classification]" :aria-label="`${day.date}: ${labels[day.classification ?? 'no_data']}`">
      <strong>{{ new Date(`${day.date}T12:00:00`).toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' }) }}</strong>
      <span>{{ day.calories_kcal == null ? '–' : `${format.format(day.calories_kcal)} kcal` }}</span>
      <span>{{ labels[day.classification ?? 'no_data'] }}</span>
      <StatusBadge v-if="day.tracking_status !== 'complete'" :status="day.tracking_status" />
    </article>
  </div>
</template>

