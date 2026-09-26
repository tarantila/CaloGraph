<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api, localizeApiError } from '../api'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { VerificationActivityResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const end = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const start = shiftIsoDate(end, -29)
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const response = ref<VerificationActivityResponse | null>(null)
const loading = ref(true)
const error = ref('')

const selectedDays = computed(() => response.value?.days.flatMap((day) => {
  const record = day.canonical
  if (record?.status !== 'available' || record.active_energy_kcal === null
    || !Number.isFinite(record.active_energy_kcal)) return []
  return [{ date: day.date, providerKey: record.provider_key, energy: record.active_energy_kcal }]
}).sort((left, right) => left.date.localeCompare(right.date)) ?? [])
const coverage = computed(() => t('analysisOverview.daysCovered', {
  count: selectedDays.value.length,
  total: 30,
}))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  response.value = null
  const params = new URLSearchParams({ start, end, view: 'canonical' })
  try {
    response.value = await api<VerificationActivityResponse>(
      `/analytics/verification/activity?${params.toString()}`,
    )
  } catch (cause) {
    error.value = localizeApiError(cause, 'analysisActivity.loadFailed')
  } finally {
    loading.value = false
  }
}

function providerLabel(providerKey: string): string {
  const key = `providerPreferencesUi.providers.${providerKey}`
  const label = t(key)
  return label === key ? providerKey : label
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('analysisActivity.title') }}</h1>
      <p>{{ t('analysisActivity.description') }}</p>
    </div>
    <RouterLink class="button secondary analysis-legacy-link" to="/aktivitaet">
      {{ t('analysisActivity.fullHistory') }}
    </RouterLink>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('common.loading') }}
  </div>
  <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
    <p>{{ error }}</p>
    <button class="button secondary analysis-retry" type="button" @click="load">
      {{ t('common.tryAgain') }}
    </button>
  </section>
  <template v-else-if="response">
    <section class="card analysis-domain-summary">
      <h2>{{ t('analysisActivity.coverage') }}</h2>
      <strong>{{ coverage }}</strong>
    </section>
    <p v-if="!selectedDays.length" class="card empty" role="status">
      {{ t('analysisActivity.empty') }}
    </p>
    <section v-else class="card analysis-data-list">
      <h2>{{ t('analysisActivity.dailyValues') }}</h2>
      <ul :aria-label="t('analysisActivity.dailyValues')">
        <li v-for="day in selectedDays" :key="day.date">
          <time :datetime="day.date">{{ formatGermanDate(day.date) }}</time>
          <span class="analysis-provider-label">{{ providerLabel(day.providerKey) }}</span>
          <strong>{{ number.format(day.energy) }} kcal</strong>
        </li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
.analysis-legacy-link { align-self: start; white-space: normal; }
.analysis-domain-summary { margin-bottom: .8rem; }
.analysis-domain-summary h2, .analysis-data-list h2 { margin-top: 0; font-size: 1.05rem; }
.analysis-domain-summary strong { font-size: 1.15rem; }
.analysis-data-list ul { list-style: none; margin: 0; padding: 0; }
.analysis-data-list li { display: grid; grid-template-columns: minmax(8rem, 1fr) minmax(7rem, 1fr) minmax(5rem, auto); gap: .5rem; padding: .65rem 0; border-top: 1px solid var(--border); }
.analysis-provider-label { color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; }
.analysis-data-list li strong { text-align: right; }
@media (max-width: 600px) {
  .analysis-data-list li { grid-template-columns: minmax(0, 1fr) minmax(5rem, auto); }
  .analysis-data-list li time, .analysis-data-list li .analysis-provider-label { grid-column: 1; }
  .analysis-data-list li strong { grid-column: 2; grid-row: 1 / span 2; align-self: center; }
}
</style>
