<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { AnalysisIcon } from '../analysis-v2'
import { formatDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import type { AnalysisNutritionMeal, AnalysisNutritionMetricKey } from '../types'

import { useAuthStore } from '../stores/auth'
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const meal = ref<AnalysisNutritionMeal | null>(null)
const loading = ref(true)
const error = ref('')
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const kcal = createNumberFormatter({ maximumFractionDigits: 0 })
let latestLoadId = 0
const contextQuery = computed(() => {
  const query: Record<string, string> = {}
  for (const key of ['period', 'start', 'end']) {
    const value = route.query[key]
    if (typeof value === 'string') query[key] = value
  }
  return query
})
const nutritionOrigin = computed(() => route.query.from === 'daily'
  ? { path: '/ernaehrung', query: typeof route.query.date === 'string' ? { date: route.query.date } : {} }
  : { path: '/analyse/ernaehrung', query: contextQuery.value })
const isAnalysisOrigin = computed(() => route.query.from !== 'daily')
const mealType = computed(() => {
  const value = meal.value?.meal_type
  return value && ['breakfast', 'lunch', 'dinner', 'snack'].includes(value)
    ? t(`nutritionVerification.mealTypes.${value}`) : t('nutritionVerification.noMealType')
})
const serving = computed(() => {
  const amount = meal.value?.serving_amount
  const unit = meal.value?.serving_unit
  if (amount == null || !Number.isFinite(amount) || !unit) return '–'
  return `${number.format(amount)} ${unit.toLowerCase() === 'portion' ? t('nutritionVerification.portionUnit', amount) : unit}`
})
const primaryNutrients = [
  { key: 'dietary_energy_kcal', label: 'calories', unit: 'kcal', color: 'var(--chart-series-violet)' },
  { key: 'protein_g', label: 'protein', unit: 'g', color: 'var(--chart-series-teal)' },
  { key: 'carbohydrates_g', label: 'carbohydrates', unit: 'g', color: 'var(--chart-series-blue)' },
  { key: 'fat_g', label: 'fat', unit: 'g', color: 'var(--chart-series-amber)' },
] as const
const secondaryNutrients: { key: AnalysisNutritionMetricKey; label: string; unit: 'g' }[] = [
  { key: 'fiber_g', label: 'fiber', unit: 'g' },
  { key: 'sugar_g', label: 'sugar', unit: 'g' },
  { key: 'saturated_fat_g', label: 'saturatedFat', unit: 'g' },
]
function formatNutrient(key: AnalysisNutritionMetricKey, unit: 'g' | 'kcal'): string {
  const value = meal.value?.[key]
  if (value == null || !Number.isFinite(value)) return '–'
  return `${(unit === 'kcal' ? kcal : number).format(value)} ${unit}`
}

function goBack(): void {
  const previousPath = router.options.history.state.back
  const previousPathname = typeof previousPath === 'string' ? previousPath.split(/[?#]/, 1)[0] : null
  if (previousPathname === '/ernaehrung' || previousPathname?.startsWith('/analyse/ernaehrung')) {
    router.back()
    return
  }
  const mealDate = meal.value?.local_date
    ?? (typeof route.query.date === 'string' ? route.query.date : null)
  void router.push(mealDate ? { path: '/ernaehrung', query: { date: mealDate } } : { path: '/ernaehrung' })
}
async function load(): Promise<void> {
  const requestUserId = auth.user?.id ?? null
  const requestId = ++latestLoadId
  if (!requestUserId) {
    meal.value = null
    error.value = ''
    loading.value = false
    return
  }
  meal.value = null
  loading.value = true
  error.value = ''
  try {
    const result = await api<AnalysisNutritionMeal>(`/analytics/nutrition/analysis/meals/${encodeURIComponent(String(route.params.eventId))}`)
    if (requestId === latestLoadId && auth.user?.id === requestUserId) meal.value = result
  } catch (cause) {
    if (requestId === latestLoadId && auth.user?.id === requestUserId) {
      error.value = localizeApiError(cause, 'nutritionVerification.detailLoadFailed')
    }
  } finally {
    if (requestId === latestLoadId && auth.user?.id === requestUserId) loading.value = false
  }
}
watch(() => [route.params.eventId, auth.user?.id ?? null] as const, ([eventId, userId]) => {
  if (typeof eventId === 'string' && userId) {
    void load()
  } else {
    latestLoadId += 1
    meal.value = null
    error.value = ''
    loading.value = false
  }
}, { immediate: true, flush: 'sync' })

onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="nutrition-meal-detail">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink v-if="isAnalysisOrigin" :to="{ path: '/analyse', query: contextQuery }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span v-if="isAnalysisOrigin" aria-hidden="true">›</span>
      <RouterLink :to="nutritionOrigin">{{ t('analysisV2.categories.nutrition') }}</RouterLink>
      <template v-if="meal">
        <span aria-hidden="true">›</span>
        <RouterLink :to="{ path: '/ernaehrung', query: { date: meal.local_date } }">{{ formatDate(meal.local_date) }}</RouterLink>
        <span aria-hidden="true">›</span>
        <span aria-current="page">{{ meal.display_name }}</span>
      </template>
      <span v-else aria-current="page">{{ t('nutritionVerification.detailTitle') }}</span>
    </nav>
    <div class="page-heading nutrition-meal-heading">
      <h1><AnalysisIcon icon="nutrition" />{{ meal?.display_name || t('nutritionVerification.detailTitle') }}</h1>
      <button class="button secondary nutrition-meal-back" data-meal-back type="button" @click="goBack">
        <span aria-hidden="true">←</span>{{ t('common.back') }}
      </button>
    </div>
    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('common.tryAgain') }}</button>
    </section>
    <template v-else-if="meal">
      <div class="nutrition-meal-metadata">
        <time :datetime="meal.local_date">{{ formatDate(meal.local_date) }}</time>
        <span v-if="meal.local_time">{{ meal.local_time.slice(0, 5) }}</span>
        <span>{{ mealType }}</span>
      </div>
      <slot name="media" />
      <section class="card nutrition-meal-nutrients" aria-labelledby="meal-nutrients-heading">
        <div class="nutrition-meal-nutrients-header">
          <h2 id="meal-nutrients-heading">{{ t('nutritionVerification.nutrients') }}</h2>
          <p>{{ t('analysisV2.nutritionMealQuantity') }}: <strong>{{ serving }}</strong></p>
        </div>
        <div class="nutrition-meal-primary-grid">
          <div
            v-for="nutrient in primaryNutrients"
            :key="nutrient.key"
            class="nutrition-meal-primary-value"
            :style="{ '--nutrient-color': nutrient.color }"
          >
            <span>{{ t(`nutritionVerification.${nutrient.label}`) }}</span>
            <strong class="number">{{ formatNutrient(nutrient.key, nutrient.unit) }}</strong>
          </div>
        </div>
        <h3 class="nutrition-meal-secondary-heading">{{ t('analysisV2.nutritionMoreNutrients') }}</h3>
        <dl class="nutrition-meal-secondary-grid">
          <div v-for="nutrient in secondaryNutrients" :key="nutrient.key" class="nutrition-meal-secondary-value">
            <dt>{{ t(`nutritionVerification.${nutrient.label}`) }}</dt>
            <dd class="number">{{ formatNutrient(nutrient.key, nutrient.unit) }}</dd>
          </div>
        </dl>
      </section>
    </template>
  </div>
</template>

<style scoped>
.nutrition-meal-detail { display: grid; gap: 1rem; min-width: 0; }
.page-heading.nutrition-meal-heading { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: .65rem 1rem; margin: 0; min-width: 0; }
.nutrition-meal-heading h1 { display: flex; align-items: center; flex: 1 1 20rem; gap: .65rem; min-width: 0; margin: 0; overflow-wrap: anywhere; }
.nutrition-meal-heading h1 svg { flex-shrink: 0; color: var(--accent); }
.nutrition-meal-back { display: inline-flex; align-items: center; gap: .4rem; flex: 0 0 auto; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-breadcrumbs a:focus-visible, .nutrition-meal-back:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.nutrition-meal-metadata { display: flex; flex-wrap: wrap; gap: .35rem 1rem; min-width: 0; color: var(--muted-strong); font-size: .9rem; overflow-wrap: anywhere; }
.nutrition-meal-nutrients { min-width: 0; margin-top: 0; }
.nutrition-meal-nutrients-header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: .5rem 1rem; padding: 1rem 1.2rem; }
.nutrition-meal-nutrients-header h2 { margin: 0; }
.nutrition-meal-nutrients-header p { margin: 0; color: var(--muted-strong); font-size: .9rem; overflow-wrap: anywhere; }
.nutrition-meal-primary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .75rem; min-width: 0; padding: 0 1.2rem 1rem; }
.nutrition-meal-primary-value { display: grid; align-content: start; gap: .35rem; min-width: 0; border: 1px solid var(--border); border-radius: .55rem; padding: .8rem; background: var(--surface-soft); }
.nutrition-meal-primary-value > span { min-width: 0; color: var(--muted-strong); overflow-wrap: anywhere; }
.nutrition-meal-primary-value strong { color: var(--nutrient-color); font-size: 1.5rem; line-height: 1.2; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.nutrition-meal-secondary-heading { margin: 0; padding: .35rem 1.2rem .5rem; }
.nutrition-meal-secondary-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 .9rem; min-width: 0; margin: 0; padding: 0 1.2rem 1rem; }
.nutrition-meal-secondary-value { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: baseline; gap: .5rem; min-width: 0; border-top: 1px solid var(--border); padding: .55rem .1rem; }
.nutrition-meal-secondary-value dt { min-width: 0; color: var(--muted-strong); overflow-wrap: anywhere; }
.nutrition-meal-secondary-value dd { min-width: 0; margin: 0; color: var(--ink); font-variant-numeric: tabular-nums; text-align: right; overflow-wrap: anywhere; }
@media (max-width: 1100px) {
  .nutrition-meal-primary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .nutrition-meal-heading { flex-direction: column; align-items: flex-start; }
  .nutrition-meal-heading h1 { flex-basis: auto; }
  .nutrition-meal-secondary-grid { grid-template-columns: minmax(0, 1fr); }
}
</style>
