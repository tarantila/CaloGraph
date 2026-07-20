<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import type { DailyPoint } from '../types'

interface Quality { missing_days: string[]; incomplete_days: DailyPoint[]; unknown_types: string[]; failed_records: number; imports: Array<{ id: string; status: string; source_type: string; started_at: string; failed: number }> }
const quality = ref<Quality | null>(null)
onMounted(async () => { quality.value = await api<Quality>('/analytics/data-quality') })
</script>

<template>
  <div class="page-heading"><div><h1>Datenqualität</h1><p>Fehlende, ungewöhnliche und fehlerhafte Daten transparent nachvollziehen.</p></div></div>
  <div v-if="quality" class="stats-grid">
    <article class="card stat-card"><div class="label">Tage ohne Daten</div><div class="value">{{ quality.missing_days.length }}</div></article>
    <article class="card stat-card"><div class="label">Unvollständige Tage</div><div class="value">{{ quality.incomplete_days.length }}</div></article>
    <article class="card stat-card"><div class="label">Unbekannte Typen</div><div class="value">{{ quality.unknown_types.length }}</div></article>
    <article class="card stat-card"><div class="label">Fehlerhafte Datensätze</div><div class="value">{{ quality.failed_records }}</div></article>
  </div>
  <section v-if="quality" class="card table-card"><h2 style="padding: 1rem 1rem 0">Auffällige Tage</h2><div class="table-scroll"><table><thead><tr><th>Datum</th><th>Status</th><th>Begründung</th></tr></thead><tbody><tr v-for="day in quality.incomplete_days" :key="day.date"><td>{{ day.date }}</td><td><StatusBadge :status="day.tracking_status" /></td><td>{{ day.tracking_reasons.join(' · ') }}</td></tr><tr v-if="!quality.incomplete_days.length"><td colspan="3" class="empty">Keine auffälligen Tage im Zeitraum.</td></tr></tbody></table></div></section>
  <section v-if="quality?.unknown_types.length" class="card form-card" style="margin-top: 1rem"><h2>Unbekannte Importtypen</h2><p>{{ quality.unknown_types.join(', ') }}</p></section>
</template>
