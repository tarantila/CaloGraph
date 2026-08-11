<script setup lang="ts">
import {
  PhArrowsClockwise,
  PhCheckCircle,
  PhDatabase,
  PhInfo,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api'
import DateFilter from '../components/DateFilter.vue'
import { formatGermanDateTime, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { useAuthStore } from '../stores/auth'

type NutrientStatus =
  | 'no_data'
  | 'insufficient_data'
  | 'below_orientation'
  | 'covered'

interface Nutrient {
  id: string
  metric_type: string
  label: string
  category: 'vitamin' | 'mineral'
  unit: 'mg' | 'ug'
  eu_nrv: number | null
  total: number | null
  average_daily: number | null
  days_with_value: number
  coverage_ratio: number
  percent_of_nrv: number | null
  status: NutrientStatus
}

interface MicronutrientResponse {
  start_date: string
  end_date: string
  source: string | null
  recorded_days: number
  last_updated_at: string | null
  available_sources: Array<{ source_type: string; last_updated_at: string | null }>
  nutrients: Nutrient[]
  definition?: {
    coverage_threshold: number
    orientation_threshold_percent: number
  }
}

const route = useRoute()
const router = useRouter()
const today = isoDateInTimeZone(useAuthStore().user?.timezone ?? 'UTC')
const before = shiftIsoDate(today, -29)
const start = ref(String(route.query.start ?? before))
const end = ref(String(route.query.end ?? today))
const source = ref(String(route.query.source ?? 'yazio_export_v1'))
const result = ref<MicronutrientResponse | null>(null)
const error = ref('')
const loading = ref(true)
const syncingHistory = ref(false)
const syncMessage = ref('')
const syncError = ref('')

const number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 2 })
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const percent = new Intl.NumberFormat('de-DE', { style: 'percent', maximumFractionDigits: 0 })
const referencePercent = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

const sourceLabels: Record<string, string> = {
  yazio_export_v1: 'YAZIO',
  health_auto_export_v2: 'Health Auto Export',
  calograph_sync_v1: 'CaloGraph Sync',
  apple_health_xml: 'Apple Health',
}

function sourceLabel(value: string) {
  return sourceLabels[value] ?? value
}

async function load(allowSourceFallback = true) {
  error.value = ''
  loading.value = true
  await router.replace({
    query: {
      start: start.value,
      end: end.value,
      source: source.value || undefined,
    },
  })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (source.value) params.set('source', source.value)
    const response = await api<MicronutrientResponse>(`/analytics/micronutrients?${params}`)
    const selectedSourceExists = response.available_sources.some(
      (item) => item.source_type === source.value,
    )
    if (allowSourceFallback && !selectedSourceExists && response.available_sources.length) {
      source.value = response.available_sources[0].source_type
      await load(false)
      return
    }
    result.value = response
  } catch (cause) {
    error.value =
      cause instanceof ApiError
        ? cause.message
        : 'Mikronährstoffanalyse konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const available = computed(
  () => result.value?.nutrients.filter((item) => item.average_daily != null) ?? [],
)
const reliable = computed(
  () => available.value.filter((item) => item.coverage_ratio >= 0.7),
)
const belowOrientation = computed(
  () => available.value.filter((item) => item.status === 'below_orientation'),
)
const vitamins = computed(
  () => result.value?.nutrients.filter((item) => item.category === 'vitamin') ?? [],
)
const minerals = computed(
  () => result.value?.nutrients.filter((item) => item.category === 'mineral') ?? [],
)
const freshness = computed(() => {
  if (!result.value?.last_updated_at) return 'Noch keine Daten'
  return `Aktualisiert ${formatGermanDateTime(result.value.last_updated_at)}`
})

function unitLabel(unit: Nutrient['unit']) {
  return unit === 'ug' ? 'µg' : unit
}

function amountLabel(item: Nutrient) {
  return item.average_daily == null
    ? '–'
    : `${number.format(item.average_daily)} ${unitLabel(item.unit)}`
}

function referenceAmountLabel(item: Nutrient) {
  return item.eu_nrv == null
    ? 'Kein EU-Referenzwert festgelegt'
    : `EU-Referenzwert ${number.format(item.eu_nrv)} ${unitLabel(item.unit)}`
}

function statusLabel(item: Nutrient) {
  if (item.status === 'no_data') return 'Keine Daten'
  if (item.status === 'insufficient_data') return 'Noch zu wenige Angaben'
  if (item.status === 'below_orientation') return 'Unter Orientierung'
  return 'Orientierung erreicht'
}

function nrvLabel(item: Nutrient) {
  if (item.eu_nrv == null) return 'Kein EU-NRV'
  if (item.percent_of_nrv == null) return '–'
  if (item.percent_of_nrv > 0 && item.percent_of_nrv < 0.1) return '< 0,1 %'
  return `${referencePercent.format(item.percent_of_nrv)} %`
}

function requiredCoverageDays() {
  const recordedDays = result.value?.recorded_days ?? 0
  const threshold = result.value?.definition?.coverage_threshold ?? 0.7
  return Math.ceil(recordedDays * threshold)
}

function coverageLabel(item: Nutrient) {
  const recordedDays = result.value?.recorded_days ?? 0
  if (!recordedDays) return 'Noch keine Ernährungstage im Zeitraum'
  const coverage = percent.format(item.coverage_ratio)
  const base = `${item.days_with_value} von ${recordedDays} Tagen mit Angaben (${coverage})`
  const requiredDays = requiredCoverageDays()
  return item.days_with_value < requiredDays
    ? `${base} · mindestens ${requiredDays} nötig`
    : base
}

async function syncYazioHistory() {
  syncingHistory.value = true
  syncMessage.value = ''
  syncError.value = ''
  try {
    const summary = await api<{ inserted: number; updated: number; skipped: number }>(
      '/yazio/sync?days=60',
      { method: 'POST' },
    )
    syncMessage.value = `${integer.format(summary.inserted + summary.updated)} Werte wurden neu übernommen oder aktualisiert.`
    await load()
  } catch (cause) {
    syncError.value =
      cause instanceof ApiError
        ? cause.message
        : 'Die YAZIO-Historie konnte nicht nachgeladen werden.'
  } finally {
    syncingHistory.value = false
  }
}
</script>

<template>
  <div class="page-heading micronutrient-heading">
    <div>
      <h1>Mikronährstoffanalyse</h1>
      <p>Vitamine und Mineralstoffe aus deinen Ernährungseinträgen – ohne Aktivitäts- oder Flüssigkeitsdaten.</p>
    </div>
    <DateFilter v-model:start="start" v-model:end="end" @apply="load()" />
  </div>

  <section class="card filter-panel micronutrient-source-filter" aria-label="Datenquelle wählen">
    <label class="field">
      Datenquelle
      <select v-model="source" @change="load()">
        <option
          v-for="item in result?.available_sources ?? []"
          :key="item.source_type"
          :value="item.source_type"
        >
          {{ sourceLabel(item.source_type) }}
        </option>
        <option
          v-if="!result?.available_sources.some((item) => item.source_type === source)"
          :value="source"
        >
          {{ sourceLabel(source) }}
        </option>
      </select>
    </label>
    <span class="source-freshness">{{ freshness }}</span>
  </section>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Mikronährstoffe werden ausgewertet …</div>
  <template v-else-if="result">
    <section class="insight-strip" aria-label="Mikronährstoffkennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>Erfasste Nährstoffe</small><strong>{{ available.length }} von {{ result.nutrients.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>Ausreichende Datenbasis</small><strong>{{ reliable.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>Unter Orientierung</small><strong>{{ belowOrientation.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>Ernährungstage</small><strong>{{ result.recorded_days }}</strong></span>
      </article>
    </section>

    <div class="micronutrient-columns">
      <section class="card micronutrient-card">
        <div class="section-card-header">
          <div><h2>Vitamine</h2><p>Durchschnitt pro Ernährungstag im gewählten Zeitraum.</p></div>
        </div>
        <div class="nutrient-list">
          <article v-for="item in vitamins" :key="item.id" class="nutrient-row">
            <div class="nutrient-row-top">
              <div><strong>{{ item.label }}</strong><small>{{ referenceAmountLabel(item) }}</small></div>
              <div class="nutrient-value"><strong>{{ amountLabel(item) }}</strong><small>Ø pro Ernährungstag</small></div>
            </div>
            <div v-if="item.percent_of_nrv != null" class="nutrient-progress-heading">
              <span>Anteil am EU-Referenzwert</span>
              <strong>{{ nrvLabel(item) }}</strong>
            </div>
            <progress
              v-if="item.percent_of_nrv != null"
              :class="['nutrient-progress', item.status]"
              :value="Math.min(item.percent_of_nrv ?? 0, 150)"
              max="150"
              :aria-label="`${item.label}: ${nrvLabel(item)} des EU-Referenzwerts`"
            >
              {{ nrvLabel(item) }}
            </progress>
            <div class="nutrient-row-meta">
              <span :class="['nutrient-status', item.status]">{{ statusLabel(item) }}</span>
              <span>{{ coverageLabel(item) }}</span>
            </div>
          </article>
        </div>
      </section>

      <section class="card micronutrient-card">
        <div class="section-card-header">
          <div><h2>Mineralstoffe</h2><p>Durchschnitt pro Ernährungstag im gewählten Zeitraum.</p></div>
        </div>
        <div class="nutrient-list">
          <article v-for="item in minerals" :key="item.id" class="nutrient-row">
            <div class="nutrient-row-top">
              <div><strong>{{ item.label }}</strong><small>{{ referenceAmountLabel(item) }}</small></div>
              <div class="nutrient-value"><strong>{{ amountLabel(item) }}</strong><small>Ø pro Ernährungstag</small></div>
            </div>
            <div v-if="item.percent_of_nrv != null" class="nutrient-progress-heading">
              <span>Anteil am EU-Referenzwert</span>
              <strong>{{ nrvLabel(item) }}</strong>
            </div>
            <progress
              v-if="item.percent_of_nrv != null"
              :class="['nutrient-progress', item.status]"
              :value="Math.min(item.percent_of_nrv ?? 0, 150)"
              max="150"
              :aria-label="`${item.label}: ${nrvLabel(item)} des EU-Referenzwerts`"
            >
              {{ nrvLabel(item) }}
            </progress>
            <div class="nutrient-row-meta">
              <span :class="['nutrient-status', item.status]">{{ statusLabel(item) }}</span>
              <span>{{ coverageLabel(item) }}</span>
            </div>
          </article>
        </div>
      </section>
    </div>

    <section class="card quality-explainer micronutrient-explainer">
      <span class="quality-explainer-icon"><PhInfo :size="22" weight="fill" /></span>
      <div>
        <h2>So ist die Auswertung zu lesen</h2>
        <p>Der Balken zeigt den berechneten Anteil am EU-Nährstoffbezugswert für Erwachsene. 100 % entsprechen dem Referenzwert; die Balkenskala reicht bis 150 %. Das Tagesmittel teilt die gemeldete Summe durch alle Ernährungstage im Zeitraum.</p>
        <p>„Noch zu wenige Angaben“ bedeutet: YAZIO hat diesen Nährstoff an weniger als 70 % der Ernährungstage geliefert. Bei {{ result.recorded_days }} Ernährungstagen sind Angaben an mindestens {{ requiredCoverageDays() }} Tagen nötig. Das sagt nichts darüber aus, ob du zu wenig davon gegessen hast.</p>
        <p>„Unter Orientierung“ bedeutet weniger als 80 % des Referenzwerts bei ausreichender Datenabdeckung. Das ist keine Diagnose eines Mangels: Produktdaten können unvollständig sein, individuelle Bedarfe unterscheiden sich und Blutwerte werden hier nicht bewertet.</p>
        <a href="https://eur-lex.europa.eu/legal-content/DE-EN/ALL/?uri=CELEX:32011R1169" target="_blank" rel="noreferrer">Referenz: Verordnung (EU) Nr. 1169/2011, Anhang XIII</a>
        <div v-if="source === 'yazio_export_v1'" class="micronutrient-backfill">
          <p>Fehlen ältere Mikronährstoffwerte, kannst du einmalig die letzten 60 Tage nachladen. Die automatische Synchronisierung bleibt weiterhin auf den kurzen Zeitraum aus deinen Kontoeinstellungen begrenzt.</p>
          <button class="button secondary" type="button" :disabled="syncingHistory" @click="syncYazioHistory">
            <PhArrowsClockwise :size="16" weight="bold" aria-hidden="true" />
            {{ syncingHistory ? 'YAZIO-Historie wird geladen …' : '60 Tage aus YAZIO nachladen' }}
          </button>
          <small v-if="syncMessage" class="micronutrient-sync-message">{{ syncMessage }}</small>
          <small v-if="syncError" class="micronutrient-sync-message error">{{ syncError }}</small>
        </div>
      </div>
    </section>

    <p class="micronutrient-source-note">
      Quelle: {{ sourceLabel(result.source ?? source) }} · Fehlende Mikronährstoffangaben werden im Tagesmittel als 0 berücksichtigt und über die Datenabdeckung sichtbar gemacht.
    </p>
  </template>
</template>
