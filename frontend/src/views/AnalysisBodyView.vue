<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api, localizeApiError } from '../api'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { WeightResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const end = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const start = shiftIsoDate(end, -29)
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const response = ref<WeightResponse | null>(null)
const loading = ref(true)
const error = ref('')

const points = computed(() => {
  const byDate = new Map((response.value?.points ?? []).map((point) => [point.date, point]))
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date))
})
const latest = computed(() => points.value.at(-1) ?? null)
const coverage = computed(() => t('analysisOverview.daysCovered', {
  count: points.value.length,
  total: 30,
}))

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  response.value = null
  const params = new URLSearchParams({ start, end })
  try {
    response.value = await api<WeightResponse>(`/analytics/weight?${params.toString()}`)
  } catch (cause) {
    error.value = localizeApiError(cause, 'analysisBody.loadFailed')
  } finally {
    loading.value = false
  }
}

function formatWeight(value: number): string {
  return `${number.format(value)} kg`
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('analysisBody.title') }}</h1>
      <p>{{ t('analysisBody.description') }}</p>
    </div>
    <RouterLink class="button secondary analysis-legacy-link" to="/gewicht">
      {{ t('analysisBody.fullHistory') }}
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
    <section class="analysis-body-summary">
      <article class="card">
        <h2>{{ t('analysisBody.coverage') }}</h2>
        <strong>{{ coverage }}</strong>
      </article>
      <article class="card">
        <h2>{{ t('analysisBody.latest') }}</h2>
        <strong v-if="latest">{{ formatWeight(latest.weight_kg) }}</strong>
        <strong v-else>–</strong>
        <time v-if="latest" :datetime="latest.date">{{ formatGermanDate(latest.date) }}</time>
      </article>
    </section>
    <p v-if="!points.length" class="card empty" role="status">
      {{ t('analysisBody.empty') }}
    </p>
    <section v-else class="card analysis-data-list">
      <h2>{{ t('analysisBody.history') }}</h2>
      <ul :aria-label="t('analysisBody.history')">
        <li v-for="point in points" :key="point.date">
          <time :datetime="point.date">{{ formatGermanDate(point.date) }}</time>
          <strong>{{ formatWeight(point.weight_kg) }}</strong>
        </li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
.analysis-legacy-link { align-self: start; white-space: normal; }
.analysis-body-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; margin-bottom: .8rem; }
.analysis-body-summary h2, .analysis-data-list h2 { margin-top: 0; font-size: 1.05rem; }
.analysis-body-summary strong { display: block; overflow-wrap: anywhere; font-size: 1.15rem; }
.analysis-body-summary time { display: block; margin-top: .35rem; color: var(--muted); }
.analysis-data-list ul { list-style: none; margin: 0; padding: 0; }
.analysis-data-list li { display: flex; justify-content: space-between; gap: .8rem; padding: .65rem 0; border-top: 1px solid var(--border); }
@media (max-width: 600px) { .analysis-body-summary { grid-template-columns: 1fr; } }
</style>
