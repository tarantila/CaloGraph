<script setup lang="ts">
import {
  PhCalendarX,
  PhCheckCircle,
  PhDatabase,
  PhInfo,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, formatGermanDateTime, shiftIsoDate } from '../date-format'
import type { DailyPoint } from '../types'

interface QualityImport {
  id: string
  status: string
  source_type: string
  client_identifier: string | null
  started_at: string
  finished_at: string | null
  received: number
  inserted: number
  updated: number
  skipped: number
  failed: number
  unknown_types: string[]
  error_message: string | null
}

interface Quality {
  start_date: string
  end_date: string
  total_days: number
  recorded_days: number
  coverage_ratio: number
  missing_days: string[]
  incomplete_days: DailyPoint[]
  unknown_types: string[]
  failed_records: number
  imports: QualityImport[]
}

const quality = ref<Quality | null>(null)
const error = ref('')
const loading = ref(true)
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const percent = new Intl.NumberFormat('de-DE', { style: 'percent', maximumFractionDigits: 0 })

onMounted(async () => {
  try {
    quality.value = await api<Quality>('/analytics/data-quality')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Datenstatus konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
})

function dateLabel(value: string) {
  return formatGermanDate(value)
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    yazio_export_v1: 'YAZIO',
    health_auto_export_v2: 'Health Auto Export',
    calograph_sync_v1: 'CaloGraph Sync',
    apple_health_xml: 'Apple Health',
    synthetic_demo: 'Demodaten',
  }
  return labels[source] ?? source
}

const missingRanges = computed(() => {
  const dates = quality.value?.missing_days ?? []
  if (!dates.length) return []
  const groups: Array<{ start: string; end: string }> = []
  for (const current of dates) {
    const previous = groups.at(-1)
    if (!previous) {
      groups.push({ start: current, end: current })
      continue
    }
    const expected = shiftIsoDate(previous.end, 1)
    if (expected === current) previous.end = current
    else groups.push({ start: current, end: current })
  }
  return groups.map((group) =>
    group.start === group.end
      ? dateLabel(group.start)
      : `${dateLabel(group.start)} – ${dateLabel(group.end)}`,
  )
})

const rangeLabel = computed(() =>
  quality.value
    ? `${dateLabel(quality.value.start_date)} – ${dateLabel(quality.value.end_date)}`
    : '',
)
const issueImports = computed(() =>
  quality.value?.imports.filter(
    (item) =>
      item.failed > 0 ||
      item.status === 'completed_with_errors' ||
      item.status === 'partial_failed' ||
      item.error_message ||
      item.unknown_types.length,
  ) ?? [],
)
</script>

<template>
  <div class="page-heading">
    <div><h1>Datenstatus</h1><p>Sieh, für welche Tage Ernährungs- und Kaloriendaten angekommen sind.</p></div>
    <span class="page-context">{{ rangeLabel }}</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Datenstatus wird geprüft …</div>
  <template v-else-if="quality">
    <section class="insight-strip" aria-label="Datenqualitätskennzahlen">
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>Datenabdeckung</small><strong>{{ percent.format(quality.coverage_ratio) }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhCalendarX :size="20" weight="duotone" /></span>
        <span><small>Tage ohne Ernährung</small><strong>{{ quality.missing_days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>Ohne Kalorienwert</small><strong>{{ quality.incomplete_days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>Importe mit Hinweisen</small><strong>{{ issueImports.length }}</strong></span>
      </article>
    </section>

    <section class="card quality-explainer">
      <span class="quality-explainer-icon"><PhInfo :size="22" weight="fill" /></span>
      <div>
        <h2>Wann gilt ein Tag als erfasst?</h2>
        <p>Sobald CaloGraph einen Kalorienwert erhält, wird der Tag als erfasst ausgewertet – unabhängig von der Höhe, deinem Budget, der Zahl der Mahlzeiten oder vorhandenen Makronährstoffen. Niedrige Werte werden nicht als unvollständig interpretiert. Nur Tage ganz ohne Ernährungsdaten oder mit Nährstoffdaten ohne Kalorienwert werden gesondert ausgewiesen.</p>
      </div>
    </section>

    <div class="quality-detail-grid">
      <section class="card quality-detail-card">
        <div class="section-card-header">
          <div><h2>Datenabdeckung</h2><p>{{ quality.recorded_days }} von {{ quality.total_days }} Kalendertagen enthalten Ernährungsdaten.</p></div>
        </div>
        <div class="quality-detail-content">
          <progress class="metric-progress quality-coverage-progress" :value="quality.coverage_ratio" max="1">
            {{ percent.format(quality.coverage_ratio) }}
          </progress>
          <template v-if="missingRanges.length">
            <h3>Zeiträume ohne Daten</h3>
            <ul class="quality-date-list">
              <li v-for="range in missingRanges" :key="range">{{ range }}</li>
            </ul>
            <p class="quality-help">Fehlt hier tatsächlich ein Eintrag, kannst du YAZIO auf der Übersicht manuell synchronisieren. Bewusste Fastentage werden derzeit ebenfalls als „ohne Daten“ angezeigt.</p>
          </template>
          <div v-else class="quality-ok"><PhCheckCircle :size="19" weight="fill" /> Keine Datenlücken im ausgewerteten Zeitraum.</div>
        </div>
      </section>

      <section class="card quality-detail-card">
        <div class="section-card-header">
          <div><h2>Tage ohne Kalorienwert</h2><p>Hier kamen zwar Ernährungswerte an, aber kein Kalorienwert für die Auswertung.</p></div>
        </div>
        <div class="quality-day-list">
          <article v-for="day in quality.incomplete_days" :key="day.date">
            <div><strong>{{ dateLabel(day.date) }}</strong><StatusBadge :status="day.tracking_status" /></div>
            <p>{{ day.tracking_reasons.join(' · ') }}</p>
          </article>
          <div v-if="!quality.incomplete_days.length" class="quality-ok"><PhCheckCircle :size="19" weight="fill" /> Alle erfassten Ernährungstage enthalten einen Kalorienwert.</div>
        </div>
      </section>
    </div>

    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>Letzte Importläufe</h2><p>Fehler und unbekannte Typen sind Hinweise auf nicht übernommene Datensätze.</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Zeitpunkt</th><th>Quelle</th><th>Status</th><th class="number">Empfangen</th><th class="number">Übernommen</th><th class="number">Fehler</th><th>Hinweis</th></tr></thead>
          <tbody>
            <tr v-for="item in quality.imports" :key="item.id">
              <td>{{ formatGermanDateTime(item.started_at) }}</td>
              <td><strong>{{ sourceLabel(item.source_type) }}</strong></td>
              <td><StatusBadge :status="item.status" /></td>
              <td class="number">{{ integer.format(item.received) }}</td>
              <td class="number">{{ integer.format(item.inserted + item.updated) }}</td>
              <td class="number">{{ integer.format(item.failed) }}</td>
              <td class="quality-import-note">
                {{ item.error_message || (item.unknown_types.length ? `${item.unknown_types.length} unbekannte Typen` : 'Keine Hinweise') }}
              </td>
            </tr>
            <tr v-if="!quality.imports.length"><td colspan="7" class="empty">Noch keine Importläufe vorhanden.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
