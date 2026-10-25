<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { PhBowlFood, PhCarrot, PhCoffee, PhForkKnife } from '@phosphor-icons/vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { groupNutritionMealOccasions } from '../analysis-nutrition-overview'
import NutritionDatePicker from '../components/NutritionDatePicker.vue'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
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
const route = useRoute()
const router = useRouter()
function validQueryDate(value: unknown): string | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/u.test(value) || value.startsWith('0000-')) {
    return null
  }
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
    ? value
    : null
}

const today = ref(isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const selectedDate = ref(validQueryDate(route.query.date) ?? today.value)

watch(() => auth.user?.timezone ?? 'UTC', (timezone) => {
  today.value = isoDateInTimeZone(timezone)
}, { immediate: true })



const view = ref<VerificationView>('canonical')
const response = ref<VerificationNutritionResponse | null>(null)
const loading = ref(true)
const error = ref('')
const kcal = createKcalFormatter()
const number = createNumberFormatter({ maximumFractionDigits: 1 })
let latestLoadId = 0

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

function formatMealType(value: string | null | undefined): string {
  switch (value) {
    case 'breakfast':
      return t('nutritionVerification.mealTypes.breakfast')
    case 'lunch':
      return t('nutritionVerification.mealTypes.lunch')
    case 'dinner':
      return t('nutritionVerification.mealTypes.dinner')
    case 'snack':
      return t('nutritionVerification.mealTypes.snack')
    default:
      return t('analysisV2.mealsUncategorized')
  }
}

function mealTypeIcon(value: string | null | undefined): Component {
  switch (value) {
    case 'breakfast':
      return PhCoffee
    case 'lunch':
      return PhBowlFood
    case 'dinner':
      return PhForkKnife
    case 'snack':
      return PhCarrot
    default:
      return PhForkKnife
  }
}

function groupMealEvents(events: readonly VerificationNutritionEvent[]) {
  return groupNutritionMealOccasions(events.map((event) => ({
    ...event,
    id: event.event_id,
    time: event.local_time,
    type: event.meal_type,
    name: event.display_name,
    energy: event.calories_kcal,
  })))
}

function formatMealEntryCount(count: number): string {
  const key = count === 1 ? 'analysisV2.mealEntrySingular' : 'analysisV2.mealEntryPlural'
  return t(key, { count: number.format(count) })
}

function occasionCalories(occasion: ReturnType<typeof groupMealEvents>[number]): number | null {
  if (occasion.entries.length !== 1) return occasion.totalEnergy
  const value = occasion.entries[0]?.energy
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatServing(event: VerificationNutritionEvent): string {
  if (event.serving_amount == null || !event.serving_unit) return t('nutritionVerification.servingNotSpecified')
  const unit = event.serving_unit.toLowerCase() === 'portion'
    ? t('nutritionVerification.portionUnit', event.serving_amount)
    : event.serving_unit
  return `${number.format(event.serving_amount)} ${unit}`
}

function openMealDetail(event: VerificationNutritionEvent): void {
  if (event.detail_available !== true) return
  void router.push({
    path: `/ernaehrung/eintrag/${event.event_id}`,
    query: { from: 'daily', date: selectedDate.value },
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  const requestUserId = auth.user?.id ?? null
  if (!requestUserId) {
    response.value = null
    error.value = ''
    loading.value = false
    return
  }
  const date = selectedDate.value
  const selectedView = view.value
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({
    date,
    view: selectedView,
  })
  try {
    const result = await api<VerificationNutritionResponse>(
      `/analytics/verification/nutrition?${params.toString()}`,
    )
    if (requestId === latestLoadId && auth.user?.id === requestUserId) response.value = result
  } catch (cause) {
    if (requestId === latestLoadId && auth.user?.id === requestUserId) {
      error.value = localizeApiError(cause, 'nutritionVerification.loadFailed')
    }
  } finally {
    if (requestId === latestLoadId && auth.user?.id === requestUserId) loading.value = false
  }
}

watch(() => auth.user?.id ?? null, (userId, previousUserId) => {
  if (previousUserId === undefined) return
  latestLoadId += 1
  response.value = null
  error.value = ''
  if (userId) {
    loading.value = true
    void load()
  } else {
    loading.value = false
  }
}, { flush: 'sync' })

watch(() => route.query.date, (value) => {
  const queryDate = validQueryDate(value)
  if (value !== undefined && queryDate === null) {
    const query = { ...route.query }
    delete query.date
    void router.replace({ query })
  }
  const nextDate = queryDate ?? today.value
  if (selectedDate.value === nextDate) return
  selectedDate.value = nextDate
  void load()
})

function selectView(nextView: VerificationView): void {
  if (view.value === nextView) return
  view.value = nextView
  void load()
}

function setSelectedDate(date: string): void {
  if (selectedDate.value !== date) {
    selectedDate.value = date
    void load()
  }
  if (validQueryDate(route.query.date) !== date) {
    void router.replace({ query: { ...route.query, date } })
  }
}

function shiftDay(days: number): void {
  setSelectedDate(shiftIsoDate(selectedDate.value, days))
}

function returnToToday(): void {
  today.value = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  setSelectedDate(today.value)
}

onMounted(() => {
  if (route.query.date !== undefined && validQueryDate(route.query.date) === null) {
    const query = { ...route.query }
    delete query.date
    void router.replace({ query })
  }
  void load()
})

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
      <NutritionDatePicker :selected-date="selectedDate" :today="today" @select="setSelectedDate" />
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

      <dl class="nutrition-day-summary" :aria-label="t('nutritionVerification.summary')">
        <div class="nutrition-day-summary-item nutrition-day-summary-item--energy">
          <dt class="nutrition-day-summary-label">{{ t('nutritionVerification.energy') }}</dt>
          <dd class="nutrition-day-summary-value">{{ formatCalories(group.summary.calories_kcal) }}</dd>
        </div>
        <div class="nutrition-day-summary-item nutrition-day-summary-item--protein">
          <dt class="nutrition-day-summary-label">{{ t('nutritionVerification.protein') }}</dt>
          <dd class="nutrition-day-summary-value">{{ formatGrams(group.summary.protein_g) }}</dd>
        </div>
        <div class="nutrition-day-summary-item nutrition-day-summary-item--carbohydrates">
          <dt class="nutrition-day-summary-label">{{ t('nutritionVerification.carbohydrates') }}</dt>
          <dd class="nutrition-day-summary-value">{{ formatGrams(group.summary.carbohydrates_g) }}</dd>
        </div>
        <div class="nutrition-day-summary-item nutrition-day-summary-item--fat">
          <dt class="nutrition-day-summary-label">{{ t('nutritionVerification.fat') }}</dt>
          <dd class="nutrition-day-summary-value">{{ formatGrams(group.summary.fat_g) }}</dd>
        </div>
      </dl>

      <div class="table-scroll nutrition-food-table-scroll">
        <table class="nutrition-food-table" :aria-label="t('nutritionVerification.foodName')">
          <colgroup>
            <col class="nutrition-food-column--food" />
            <col class="nutrition-food-column--serving" />
            <col class="nutrition-food-column--calories" />
            <col class="nutrition-food-column--protein" />
            <col class="nutrition-food-column--carbohydrates" />
            <col class="nutrition-food-column--fat" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">
                <h2 class="nutrition-food-section-heading">{{ t('nutritionVerification.foodName') }}</h2>
              </th>
              <th scope="col">{{ t('nutritionVerification.serving') }}</th>
              <th scope="col">{{ t('nutritionVerification.calories') }}</th>
              <th scope="col">{{ t('nutritionVerification.protein') }}</th>
              <th scope="col">{{ t('nutritionVerification.carbohydrates') }}</th>
              <th scope="col">{{ t('nutritionVerification.fat') }}</th>
            </tr>
          </thead>
          <tbody
            v-for="occasion in groupMealEvents(group.events)"
            :key="occasion.key"
            class="verification-meal-occasion"
            :data-meal-type="occasion.type ?? ''"
          >
            <tr class="verification-meal-occasion-heading">
              <th colspan="6" scope="rowgroup" class="verification-meal-occasion-title">
                <div class="verification-meal-occasion-header">
                  <div class="verification-meal-occasion-identity">
                    <span class="verification-meal-occasion-icon" aria-hidden="true">
                      <component :is="mealTypeIcon(occasion.type)" weight="regular" />
                    </span>
                    <span class="verification-meal-occasion-copy">
                      <span class="verification-meal-occasion-label">{{ formatMealType(occasion.type) }}</span>
                      <span class="verification-meal-occasion-entry-count">{{ formatMealEntryCount(occasion.entryCount) }}</span>
                    </span>
                  </div>
                  <span
                    v-if="occasionCalories(occasion) !== null"
                    class="verification-meal-occasion-total"
                  >
                    {{ formatCalories(occasionCalories(occasion)) }}
                  </span>
                </div>
              </th>
            </tr>
            <tr
              v-for="event in occasion.entries"
              :key="event.event_id"
              class="verification-event-row"
              :class="{ 'verification-event-row--navigable': event.detail_available === true }"
              :tabindex="event.detail_available === true ? 0 : undefined"
              :aria-keyshortcuts="event.detail_available === true ? 'Enter' : undefined"
              @click="openMealDetail(event)"
              @keydown.enter.stop.prevent="openMealDetail(event)"
            >
              <td class="verification-event-food"><strong>{{ event.display_name }}</strong></td>
              <td :data-label="t('nutritionVerification.serving')">{{ formatServing(event) }}</td>
              <td class="number" :data-label="t('nutritionVerification.calories')">{{ formatCalories(event.calories_kcal) }}</td>
              <td class="number" :data-label="t('nutritionVerification.protein')">{{ formatGrams(event.protein_g) }}</td>
              <td class="number" :data-label="t('nutritionVerification.carbohydrates')">{{ formatGrams(event.carbohydrates_g) }}</td>
              <td class="number" :data-label="t('nutritionVerification.fat')">{{ formatGrams(event.fat_g) }}</td>
            </tr>
          </tbody>
          <tbody v-if="!group.events.length">
            <tr><td colspan="6" class="empty">{{ t('verification.status.no_data') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<style scoped>
.nutrition-day-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: .85rem 1.05rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--surface); }
.nutrition-day-summary-item--energy { --nutrition-summary-color: var(--chart-series-violet); }
.nutrition-day-summary-item--protein { --nutrition-summary-color: var(--chart-series-teal); }
.nutrition-day-summary-item--carbohydrates { --nutrition-summary-color: var(--chart-series-blue); }
.nutrition-day-summary-item--fat { --nutrition-summary-color: var(--chart-series-amber); }
.nutrition-day-summary-item .nutrition-day-summary-label { color: var(--nutrition-summary-color); }
.nutrition-day-summary-item { min-width: 0; padding: .65rem .75rem; }
.nutrition-day-summary-item + .nutrition-day-summary-item { border-left: 1px solid var(--border); }
.nutrition-day-summary-label { color: var(--muted-strong); font-size: .65rem; line-height: 1.4; overflow-wrap: anywhere; }
.nutrition-day-summary-value { margin: .25rem 0 0; color: var(--ink); font-size: .95rem; font-weight: 650; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.nutrition-food-table-scroll { min-width: 0; width: 100%; max-width: 100%; container-type: inline-size; container-name: nutrition-food-table; }
.nutrition-food-table { width: 100%; max-width: 100%; table-layout: fixed; }
.nutrition-food-table > colgroup > .nutrition-food-column--food { width: 31.25%; }
.nutrition-food-table > colgroup > .nutrition-food-column--serving { width: 13.07%; }
.nutrition-food-table > colgroup > .nutrition-food-column--calories { width: 13.73%; }
.nutrition-food-table > colgroup > .nutrition-food-column--protein { width: 12.47%; }
.nutrition-food-table > colgroup > .nutrition-food-column--carbohydrates { width: 20.36%; }
.nutrition-food-table > colgroup > .nutrition-food-column--fat { width: 9.12%; }
.nutrition-food-table > thead th { background: var(--surface); text-align: left; }
.nutrition-food-table > thead th:first-child {
  box-shadow: inset 3px 0 0 color-mix(in srgb, var(--accent) 55%, var(--border));
  text-transform: none;
  letter-spacing: normal;
}
.nutrition-food-table .verification-event-row > td { text-align: left; }
.nutrition-food-table .verification-event-food { min-width: 0; }
.nutrition-food-section-heading { border-left: 0; padding-left: 0; color: var(--ink); text-transform: none; letter-spacing: normal; }
@container nutrition-food-table (max-width: 700px) {
  .nutrition-food-table > colgroup { display: none; }
  .nutrition-food-table,
  .nutrition-food-table > thead,
  .nutrition-food-table > tbody { display: block; width: 100%; max-width: 100%; }
  .nutrition-food-table > thead > tr,
  .nutrition-food-table > tbody > tr { display: block; }
  .nutrition-food-table > thead > tr > th { display: block; }
  .nutrition-food-table > thead > tr > th:first-child { width: 100%; padding: .7rem .75rem; }
  .nutrition-food-table > thead > tr > th:not(:first-child) {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }
  .nutrition-food-table .verification-meal-occasion-heading th { padding-inline: .75rem; }
  .nutrition-food-table .verification-meal-occasion-heading th { display: block; width: 100%; white-space: normal; }
  .nutrition-food-table .verification-event-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
  .nutrition-food-table .verification-event-row > td { display: block; min-width: 0; padding: .5rem .75rem; border-bottom: 0; white-space: normal; overflow-wrap: anywhere; }
  .nutrition-food-table .verification-event-row > .verification-event-food { grid-column: 1 / -1; }
  .nutrition-food-table .verification-event-row > td.number,
  .nutrition-food-table .verification-event-row > td:nth-child(2) { white-space: nowrap; }
  .nutrition-food-table .verification-event-row > td[data-label] { display: flex; justify-content: space-between; align-items: baseline; gap: .4rem; }
  .nutrition-food-table .verification-event-row > td[data-label]::before { content: attr(data-label); color: var(--muted); font-size: .66rem; font-weight: 400; text-align: left; }
}
@media (max-width: 480px) {
  .nutrition-day-summary { margin: .75rem; }
  .nutrition-day-summary-item { padding: .6rem .4rem; }
  .nutrition-day-summary-value { font-size: .8rem; }
}
.verification-event-row--navigable { cursor: pointer; }
.verification-event-row--navigable:hover > td { background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
:global(:root[data-theme='dark'] .nutrition-food-table .verification-event-row--navigable:hover > td),
:global(:root[data-theme='dark'] .nutrition-food-table .verification-event-row--navigable:focus-visible > td) {
  background-color: color-mix(in srgb, var(--accent) 30%, var(--surface) 70%);
}
.verification-event-row--navigable:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: -2px; }
tbody.verification-meal-occasion { --meal-accent: var(--muted); }
tbody.verification-meal-occasion[data-meal-type='breakfast'] { --meal-accent: var(--chart-series-violet); }
tbody.verification-meal-occasion[data-meal-type='lunch'] { --meal-accent: var(--chart-series-teal); }
tbody.verification-meal-occasion[data-meal-type='dinner'] { --meal-accent: var(--chart-series-blue); }
tbody.verification-meal-occasion[data-meal-type='snack'] { --meal-accent: var(--chart-series-amber); }
.verification-meal-occasion-heading th {
  padding: .75rem 1rem;
  color: var(--ink);
  font-size: .95rem;
  font-weight: 650;
  text-align: left;
  text-transform: none;
  letter-spacing: normal;
  white-space: normal;
  background: color-mix(in srgb, var(--meal-accent) 10%, var(--surface));
}
.verification-meal-occasion-header { display: flex; align-items: center; flex-wrap: wrap; gap: .75rem; min-width: 0; }
.verification-meal-occasion-identity { display: flex; align-items: center; flex: 1 1 10rem; gap: .65rem; min-width: 0; }
.verification-meal-occasion-icon {
  display: inline-grid;
  place-items: center;
  flex: 0 0 2.25rem;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: 50%;
  color: var(--ink);
  background: color-mix(in srgb, var(--meal-accent) 24%, var(--surface));
}
.verification-meal-occasion-icon :deep(svg) { width: 1.2rem; height: 1.2rem; }
.verification-meal-occasion-copy { display: grid; gap: .08rem; min-width: 0; }
.verification-meal-occasion-label { color: var(--ink); font-size: .95rem; font-weight: 650; line-height: 1.3; overflow-wrap: anywhere; }
.verification-meal-occasion-entry-count { color: var(--muted-strong); font-size: .72rem; font-weight: 400; line-height: 1.4; }
.verification-meal-occasion-total {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 100%;
  margin-inline-start: auto;
  padding: .4rem .75rem;
  border: 1px solid color-mix(in srgb, var(--meal-accent) 24%, var(--border));
  border-radius: .45rem;
  color: var(--ink);
  font-size: .9rem;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  text-align: center;
  overflow-wrap: anywhere;
  background: color-mix(in srgb, var(--meal-accent) 18%, var(--surface));
}
</style>
