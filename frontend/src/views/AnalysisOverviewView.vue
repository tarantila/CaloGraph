<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { api, localizeApiError } from '../api'
import { createNumberFormatter, i18n } from '../i18n'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { useAuthStore } from '../stores/auth'
import type {
  AnalysisMicronutrientResponse,
  DailyPoint,
  VerificationActivityResponse,
  WeightResponse,
} from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const start = shiftIsoDate(today, -29)
const end = today
const days = 30
const number = createNumberFormatter({ maximumFractionDigits: 0 })
const nutritionError = ref('')
const nutritionLoading = ref(true)
const nutritionPointCount = ref(0)
const micronutrientError = ref('')
const micronutrientLoading = ref(true)
const micronutrientCount = ref(0)
const micronutrientTotal = ref(0)
const activityError = ref('')
const activityLoading = ref(true)
const activityDayCount = ref<number | null>(null)
const bodyError = ref('')
const bodyLoading = ref(true)
const weightDayCount = ref<number | null>(null)

const query = new URLSearchParams({ start, end })
const rangeQuery = query.toString()

async function loadNutrition(): Promise<void> {
  nutritionLoading.value = true
  micronutrientLoading.value = true
  nutritionError.value = ''
  micronutrientError.value = ''
  try {
    const readiness = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${rangeQuery}`,
    )
    micronutrientTotal.value = readiness.metrics.length
    micronutrientCount.value = readiness.metrics.filter((metric) => metric.days_with_data > 0).length
    micronutrientLoading.value = false
  } catch (cause) {
    const message = localizeApiError(cause, 'analysisOverview.loadFailed')
    nutritionError.value = message
    micronutrientError.value = message
    nutritionLoading.value = false
    micronutrientLoading.value = false
    return
  }

  try {
    const trends = await api<{ points: DailyPoint[] }>(`/analytics/trends?${rangeQuery}`)
    nutritionPointCount.value = trends.points.filter((point) =>
      point.calories_kcal !== null
        || point.protein_g !== null
        || point.carbs_g !== null
        || point.fat_g !== null,
    ).length
  } catch (cause) {
    nutritionError.value = localizeApiError(cause, 'analysisOverview.loadFailed')
  } finally {
    nutritionLoading.value = false
  }
}

async function loadActivity(): Promise<void> {
  activityLoading.value = true
  activityError.value = ''
  try {
    const response = await api<VerificationActivityResponse>(
      `/analytics/verification/activity?${rangeQuery}&view=canonical`,
    )
    activityDayCount.value = response.days.filter((day) => {
      const energy = day.canonical?.active_energy_kcal
      return typeof energy === 'number' && Number.isFinite(energy)
    }).length
  } catch (cause) {
    activityError.value = localizeApiError(cause, 'analysisOverview.loadFailed')
  } finally {
    activityLoading.value = false
  }
}

async function loadBody(): Promise<void> {
  bodyLoading.value = true
  bodyError.value = ''
  try {
    const response = await api<WeightResponse>(`/analytics/weight?${rangeQuery}`)
    weightDayCount.value = new Set(response.points.map((point) => point.date)).size
  } catch (cause) {
    bodyError.value = localizeApiError(cause, 'analysisOverview.loadFailed')
  } finally {
    bodyLoading.value = false
  }
}

onMounted(() => {
  void loadNutrition()
  void loadActivity()
  void loadBody()
})

const nutritionCoverage = computed(() => {
  if (nutritionLoading.value) return t('common.loading')
  if (nutritionError.value) return nutritionError.value
  return nutritionPointCount.value > 0
    ? t('analysisOverview.daysCovered', { count: nutritionPointCount.value, total: days })
    : t('analysisOverview.noReadings')
})
const micronutrientCoverage = computed(() => {
  if (micronutrientLoading.value) return t('common.loading')
  if (micronutrientError.value) return micronutrientError.value
  return t('analysisOverview.micronutrientCoverage', {
    count: number.format(micronutrientCount.value),
    total: number.format(micronutrientTotal.value),
  })
})
const activityCoverage = computed(() => {
  if (activityLoading.value) return t('common.loading')
  if (activityError.value) return activityError.value
  return activityDayCount.value
    ? t('analysisOverview.daysCovered', { count: activityDayCount.value, total: days })
    : t('analysisOverview.noReadings')
})
const bodyCoverage = computed(() => {
  if (bodyLoading.value) return t('common.loading')
  if (bodyError.value) return bodyError.value
  return weightDayCount.value
    ? t('analysisOverview.daysCovered', { count: weightDayCount.value, total: days })
    : t('analysisOverview.noReadings')
})
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('analysisOverview.title') }}</h1>
      <p>{{ t('analysisOverview.description') }}</p>
    </div>
  </div>
  <section v-if="nutritionError || micronutrientError" class="card error analysis-overview-error" role="alert" aria-live="assertive">
    <p>{{ nutritionError || micronutrientError }}</p>
    <button class="button secondary" type="button" @click="loadNutrition">
      {{ t('common.tryAgain') }}
    </button>
  </section>

  <nav class="analysis-domain-grid" :aria-label="t('analysisOverview.areas')">
    <RouterLink class="card analysis-domain-link" to="/analyse/ernaehrung">
      <span class="analysis-domain-icon" aria-hidden="true">⌁</span>
      <h2>{{ t('analysisOverview.nutritionTitle') }}</h2>
      <p>{{ t('analysisOverview.nutritionDescription') }}</p>
      <strong>{{ nutritionCoverage }}</strong>
    </RouterLink>
    <RouterLink class="card analysis-domain-link" to="/analyse/mikronaehrstoffe">
      <span class="analysis-domain-icon" aria-hidden="true">⊙</span>
      <h2>{{ t('analysisOverview.micronutrientTitle') }}</h2>
      <p>{{ t('analysisOverview.micronutrientDescription') }}</p>
      <strong>{{ micronutrientCoverage }}</strong>
    </RouterLink>
    <RouterLink class="card analysis-domain-link" to="/analyse/aktivitaet">
      <span class="analysis-domain-icon" aria-hidden="true">↗</span>
      <h2>{{ t('analysisOverview.activityTitle') }}</h2>
      <p>{{ t('analysisOverview.activityDescription') }}</p>
      <strong>{{ activityCoverage }}</strong>
    </RouterLink>
    <RouterLink class="card analysis-domain-link" to="/analyse/koerper">
      <span class="analysis-domain-icon" aria-hidden="true">◉</span>
      <h2>{{ t('analysisOverview.bodyTitle') }}</h2>
      <p>{{ t('analysisOverview.bodyDescription') }}</p>
      <strong>{{ bodyCoverage }}</strong>
    </RouterLink>
  </nav>
  <div v-if="activityError || bodyError" class="analysis-overview-retries">
    <button
      v-if="activityError"
      class="button secondary analysis-overview-retry"
      data-analysis-retry="activity"
      type="button"
      @click="loadActivity"
    >
      {{ t('analysisOverview.activityTitle') }} · {{ t('common.tryAgain') }}
    </button>
    <button
      v-if="bodyError"
      class="button secondary analysis-overview-retry"
      data-analysis-retry="body"
      type="button"
      @click="loadBody"
    >
      {{ t('analysisOverview.bodyTitle') }} · {{ t('common.tryAgain') }}
    </button>
  </div>
</template>

<style scoped>
.analysis-domain-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
.analysis-overview-retries { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: .9rem; }
.analysis-domain-link {
  display: grid;
  grid-template-columns: 2.75rem minmax(0, 1fr);
  align-content: start;
  column-gap: .9rem;
  row-gap: .25rem;
  color: inherit;
  text-decoration: none;
  transition: border-color .16s ease, transform .16s ease;
}
.analysis-domain-link:hover { border-color: var(--accent); transform: translateY(-2px); }
.analysis-domain-link:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
.analysis-domain-link h2 { margin: 0; font-size: 1.08rem; }
.analysis-domain-link p { grid-column: 2; margin: 0; color: var(--muted); }
.analysis-domain-link strong { grid-column: 2; margin-top: .45rem; color: var(--muted-strong); font-size: .88rem; overflow-wrap: anywhere; }
.analysis-domain-icon {
  grid-row: span 3;
  display: grid;
  place-items: center;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: .8rem;
  background: color-mix(in srgb, var(--accent) 14%, transparent);
  color: var(--accent);
  font-size: 1.35rem;
}
@media (max-width: 640px) {
  .analysis-domain-grid { grid-template-columns: 1fr; }
}
</style>
