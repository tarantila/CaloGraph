<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api, localizeApiError } from '../api'
import { formatDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createKcalFormatter, createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type {
  VerificationNutritionEvent,
  VerificationNutritionProviderGroup,
  VerificationNutritionResponse,
  VerificationView,
} from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const selectedDate = ref(isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const view = ref<VerificationView>('canonical')
const response = ref<VerificationNutritionResponse | null>(null)
const loading = ref(true)
const error = ref('')
const kcal = createKcalFormatter()
const number = createNumberFormatter({ maximumFractionDigits: 1 })

const displayedGroups = computed<VerificationNutritionProviderGroup[]>(() => {
  if (!response.value) return []
  if (view.value === 'canonical') return response.value.canonical ? [response.value.canonical] : []
  return response.value.providers
})
const hasNutritionData = computed(() => displayedGroups.value.some((group) =>
  group.events.length > 0 || Object.values(group.summary).some((value) => value != null),
))

function formatCalories(value: number | null): string {
  return value == null ? '–' : `${kcal.format(value)} ${t('common.kcal')}`
}

function formatGrams(value: number | null): string {
  return value == null ? '–' : `${number.format(value)} ${t('common.grams')}`
}

function formatServing(event: VerificationNutritionEvent): string {
  if (event.serving_amount == null || !event.serving_unit) return '–'
  return `${number.format(event.serving_amount)} ${event.serving_unit}`
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({
    date: selectedDate.value,
    view: view.value,
  })
  try {
    response.value = await api<VerificationNutritionResponse>(
      `/analytics/verification/nutrition?${params.toString()}`,
    )
  } catch (cause) {
    error.value = localizeApiError(cause, 'nutritionVerification.loadFailed')
  } finally {
    loading.value = false
  }
}

function selectView(nextView: VerificationView): void {
  if (view.value === nextView) return
  view.value = nextView
  void load()
}

function shiftDay(days: number): void {
  selectedDate.value = shiftIsoDate(selectedDate.value, days)
  void load()
}

function returnToToday(): void {
  const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  if (selectedDate.value === today) return
  selectedDate.value = today
  void load()
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('nutritionVerification.title') }}</h1>
      <p>{{ t('nutritionVerification.description') }}</p>
    </div>
  </div>

  <section class="verification-toolbar" :aria-label="t('nutritionVerification.title')">
    <div class="verification-view-toggle" role="group" :aria-label="t('nutritionVerification.title')">
      <button
        type="button"
        :aria-pressed="view === 'canonical'"
        :disabled="loading"
        @click="selectView('canonical')"
      >
        {{ t('nutritionVerification.canonicalToggle') }}
      </button>
      <button
        type="button"
        :aria-pressed="view === 'all'"
        :disabled="loading"
        @click="selectView('all')"
      >
        {{ t('nutritionVerification.allSourcesToggle') }}
      </button>
    </div>

    <div class="verification-date-navigation" role="group" :aria-label="t('common.period')">
      <button
        type="button"
        :aria-label="t('nutritionVerification.previousDay')"
        :disabled="loading"
        @click="shiftDay(-1)"
      >
        <span aria-hidden="true">‹</span>
      </button>
      <strong>{{ formatDate(selectedDate) }}</strong>
      <button
        type="button"
        :aria-label="t('nutritionVerification.nextDay')"
        :disabled="loading"
        @click="shiftDay(1)"
      >
        <span aria-hidden="true">›</span>
      </button>
    </div>

    <button class="button secondary" type="button" :disabled="loading" @click="returnToToday">
      {{ t('overview.today') }}
    </button>
  </section>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('nutritionVerification.loading') }}
  </div>
  <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
    <p>{{ error }}</p>
    <button class="button secondary" type="button" @click="load">{{ t('common.tryAgain') }}</button>
  </section>
  <section v-else-if="response" class="verification-provider-groups">
    <div v-if="!hasNutritionData" class="card empty" role="status" aria-live="polite">
      {{ t('nutritionVerification.empty') }}
    </div>

    <section
      v-for="group in displayedGroups"
      :key="group.provider_key"
      class="card table-card verification-provider-group"
      :aria-label="t(`verification.providers.${group.provider_key}`)"
    >
      <div class="verification-provider-group-header">
        <div>
          <h2>{{ t(`verification.providers.${group.provider_key}`) }}</h2>
          <p>
            {{ t('nutritionVerification.summary') }} ·
            {{ t(`verification.status.${group.status}`) }} ·
            {{ t('verification.records', { count: group.record_count }) }}
          </p>
        </div>
      </div>

      <div class="table-scroll">
        <table :aria-label="t('nutritionVerification.summary')">
          <thead>
            <tr>
              <th>{{ t('nutritionVerification.calories') }}</th>
              <th>{{ t('nutritionVerification.protein') }}</th>
              <th>{{ t('nutritionVerification.carbohydrates') }}</th>
              <th>{{ t('nutritionVerification.fat') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="number">{{ formatCalories(group.summary.calories_kcal) }}</td>
              <td class="number">{{ formatGrams(group.summary.protein_g) }}</td>
              <td class="number">{{ formatGrams(group.summary.carbohydrates_g) }}</td>
              <td class="number">{{ formatGrams(group.summary.fat_g) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="verification-provider-group-header">
        <div><h2>{{ t('nutritionVerification.events') }}</h2></div>
      </div>
      <div class="table-scroll">
        <table :aria-label="t('nutritionVerification.events')">
          <thead>
            <tr>
              <th>{{ t('nutritionVerification.occurredAt') }}</th>
              <th>{{ t('nutritionVerification.mealType') }}</th>
              <th>{{ t('nutritionVerification.foodName') }}</th>
              <th>{{ t('nutritionVerification.serving') }}</th>
              <th>{{ t('nutritionVerification.calories') }}</th>
              <th>{{ t('nutritionVerification.protein') }}</th>
              <th>{{ t('nutritionVerification.carbohydrates') }}</th>
              <th>{{ t('nutritionVerification.fat') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(event, eventIndex) in group.events" :key="`${group.provider_key}-${event.local_time}-${event.display_name}-${eventIndex}`" class="verification-event-row">
              <td>{{ event.local_time || '—' }}</td>
              <td>{{ event.meal_type || '—' }}</td>
              <td class="verification-event-food"><strong>{{ event.display_name }}</strong></td>
              <td>{{ formatServing(event) }}</td>
              <td class="number">{{ formatCalories(event.calories_kcal) }}</td>
              <td class="number">{{ formatGrams(event.protein_g) }}</td>
              <td class="number">{{ formatGrams(event.carbohydrates_g) }}</td>
              <td class="number">{{ formatGrams(event.fat_g) }}</td>
            </tr>
            <tr v-if="!group.events.length">
              <td colspan="8" class="empty">{{ t('verification.status.no_data') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
