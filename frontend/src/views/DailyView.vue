<script setup lang="ts">
import {
  PhCalendarBlank,
  PhCheckCircle,
  PhFire,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import DateFilter from '../components/DateFilter.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'

const t = i18n.global.t.bind(i18n.global)

const route = useRoute()
const router = useRouter()
const today = isoDateInTimeZone(useAuthStore().user?.timezone ?? 'UTC')
const before = shiftIsoDate(today, -29)
const start = ref(String(route.query.start ?? before))
const end = ref(String(route.query.end ?? today))
const source = ref(String(route.query.source ?? ''))
const tracking = ref(String(route.query.tracking ?? ''))
const weekday = ref(String(route.query.weekday ?? ''))
const points = ref<DailyPoint[]>([])
const error = ref('')
const loading = ref(true)
const number = createNumberFormatter({ maximumFractionDigits: 1 })

function numericValue(value: number | null | undefined) {
  if (value == null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
function numericSortValue(point: DailyPoint, column: SortColumn) {
  switch (column) {
    case 'calories_kcal': return numericValue(point.calories_kcal)
    case 'deviation_kcal': return numericValue(point.deviation_kcal)
    case 'protein_g': return numericValue(point.protein_g)
    case 'carbs_g': return numericValue(point.carbs_g)
    case 'fat_g': return numericValue(point.fat_g)
    default: return null
  }
}

type SortColumn = 'date' | 'status' | 'calories_kcal' | 'deviation_kcal' | 'protein_g' | 'carbs_g' | 'fat_g'
type SortDirection = 'asc' | 'desc'

const sortColumn = ref<SortColumn>('date')
const sortDirection = ref<SortDirection>('desc')
const statusSortRank: Record<string, number> = {
  complete: 0,
  probably_complete: 0,
  probably_incomplete: 1,
  incomplete: 1,
  no_data: 2,
}

function compareNumbers(
  left: number | null,
  right: number | null,
  direction: SortDirection,
) {
  const leftMissing = left == null
  const rightMissing = right == null
  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) return 0
    return leftMissing ? 1 : -1
  }
  const result = left - right
  return direction === 'asc' ? result : -result
}

function compareDates(left: string, right: string, direction: SortDirection) {
  const result = left.localeCompare(right)
  return direction === 'asc' ? result : -result
}

function compareStatuses(left: string, right: string, direction: SortDirection) {
  const leftRank = statusSortRank[left] ?? 3
  const rightRank = statusSortRank[right] ?? 3
  if (leftRank !== rightRank) {
    const result = leftRank - rightRank
    return direction === 'asc' ? result : -result
  }
  return 0
}

const sortedPoints = computed(() =>
  points.value
    .map((point, index) => ({ point, index }))
    .sort(({ point: left, index: leftIndex }, { point: right, index: rightIndex }) => {
      let result = 0
      if (sortColumn.value === 'date') {
        result = compareDates(left.date, right.date, sortDirection.value)
      } else if (sortColumn.value === 'status') {
        result = compareStatuses(left.tracking_status, right.tracking_status, sortDirection.value)
      } else {
        result = compareNumbers(
          numericSortValue(left, sortColumn.value),
          numericSortValue(right, sortColumn.value),
          sortDirection.value,
        )
      }
      if (result !== 0) return result
      if (left.date !== right.date) return compareDates(left.date, right.date, 'desc')
      return leftIndex - rightIndex
    })
    .map(({ point }) => point),
)

function sortBy(column: SortColumn) {
  if (sortColumn.value === column) {
    sortDirection.value = sortDirection.value === 'desc' ? 'asc' : 'desc'
    return
  }
  sortColumn.value = column
  sortDirection.value = column === 'status' ? 'asc' : 'desc'
}

function sortIndicator(column: SortColumn) {
  if (sortColumn.value !== column) return '↕'
  return sortDirection.value === 'desc' ? '↓' : '↑'
}

function ariaSort(column: SortColumn) {
  return sortColumn.value === column
    ? sortDirection.value === 'desc' ? 'descending' : 'ascending'
    : 'none'
}


async function load() {
  error.value = ''
  loading.value = true
  await router.replace({ query: { start: start.value, end: end.value, source: source.value || undefined, tracking: tracking.value || undefined, weekday: weekday.value || undefined } })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (source.value) params.set('source', source.value)
    if (tracking.value) params.set('tracking', tracking.value)
    if (weekday.value) params.set('weekday', weekday.value)
    points.value = await api<DailyPoint[]>(`/analytics/daily?${params}`)
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}
onMounted(load)
const display = (value: number | null, unit = '') => {
  const parsed = numericValue(value)
  return parsed == null ? '–' : `${number.format(parsed)}${unit}`
}
const recordedPoints = computed(() =>
  points.value.filter((point) => numericValue(point.calories_kcal) != null),
)
const averageCalories = computed(() => {
  const values = recordedPoints.value
    .map((point) => numericValue(point.calories_kcal))
    .filter((value): value is number => value != null)
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
})
const missingPoints = computed(() =>
  points.value.filter((point) => point.tracking_status === 'no_data'),
)
const weekdays = computed(() =>
  ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map((key) =>
    t(`weekdays.${key}`),
  ),
)
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('daily.title') }}</h1><p>{{ t('daily.description') }}</p></div>
    <DateFilter v-model:start="start" v-model:end="end" @apply="load" />
  </div>
  <section class="card filter-panel" :aria-label="t('daily.filterAria')">
    <div class="filters">
      <label class="field">{{ t('daily.dataSource') }}<input v-model="source" :placeholder="t('daily.allSources')" /></label>
      <label class="field">{{ t('daily.dataStatus') }}<select v-model="tracking"><option value="">{{ t('common.all') }}</option><option value="complete,probably_complete">{{ t('daily.withCalories') }}</option><option value="probably_incomplete,incomplete">{{ t('daily.withoutCalories') }}</option><option value="no_data">{{ t('daily.noNutrition') }}</option></select></label>
      <label class="field">{{ t('daily.weekday') }}<select v-model="weekday"><option value="">{{ t('common.all') }}</option><option v-for="(label, index) in weekdays" :key="label" :value="String(index)">{{ label }}</option></select></label>
      <button class="button secondary" type="button" @click="load">{{ t('common.apply') }}</button>
    </div>
  </section>
  <div v-if="error" class="card error">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('daily.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('daily.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('daily.daysInPeriod') }}</small><strong>{{ points.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhFire :size="20" weight="duotone" /></span>
        <span><small>{{ t('calendar.averageCalories') }}</small><strong>{{ averageCalories == null ? '–' : `${number.format(averageCalories)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('daily.withCalories') }}</small><strong>{{ recordedPoints.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('daily.noNutrition') }}</small><strong>{{ missingPoints.length }}</strong></span>
      </article>
    </section>
    <section class="card table-card daily-table">
      <div class="section-card-header">
        <div><h2>{{ t('daily.individualDays') }}</h2><p>{{ t('daily.missingEmpty') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th :aria-sort="ariaSort('date')"><button class="table-sort-button" type="button" :aria-label="t('daily.sortBy', { column: t('common.date') })" @click="sortBy('date')">{{ t('common.date') }} <span aria-hidden="true">{{ sortIndicator('date') }}</span></button></th>
            <th :aria-sort="ariaSort('status')"><button class="table-sort-button" type="button" :aria-label="t('daily.sortBy', { column: t('common.status') })" @click="sortBy('status')">{{ t('common.status') }} <span aria-hidden="true">{{ sortIndicator('status') }}</span></button></th>
            <th class="number" :aria-sort="ariaSort('calories_kcal')"><button class="table-sort-button number" type="button" :aria-label="t('daily.sortBy', { column: t('charts.calories') })" @click="sortBy('calories_kcal')">{{ t('charts.calories') }} <span aria-hidden="true">{{ sortIndicator('calories_kcal') }}</span></button></th>
            <th class="number" :aria-sort="ariaSort('deviation_kcal')"><button class="table-sort-button number" type="button" :aria-label="t('daily.sortBy', { column: t('common.deviation') })" @click="sortBy('deviation_kcal')">{{ t('common.deviation') }} <span aria-hidden="true">{{ sortIndicator('deviation_kcal') }}</span></button></th>
            <th class="number" :aria-sort="ariaSort('protein_g')"><button class="table-sort-button number" type="button" :aria-label="t('daily.sortBy', { column: t('charts.protein') })" @click="sortBy('protein_g')">{{ t('charts.protein') }} <span aria-hidden="true">{{ sortIndicator('protein_g') }}</span></button></th>
            <th class="number" :aria-sort="ariaSort('carbs_g')"><button class="table-sort-button number" type="button" :aria-label="t('daily.sortBy', { column: t('charts.carbs') })" @click="sortBy('carbs_g')">{{ t('charts.carbs') }} <span aria-hidden="true">{{ sortIndicator('carbs_g') }}</span></button></th>
            <th class="number" :aria-sort="ariaSort('fat_g')"><button class="table-sort-button number" type="button" :aria-label="t('daily.sortBy', { column: t('charts.fat') })" @click="sortBy('fat_g')">{{ t('charts.fat') }} <span aria-hidden="true">{{ sortIndicator('fat_g') }}</span></button></th>
          </tr></thead>
          <tbody>
            <tr v-for="point in sortedPoints" :key="point.date">
              <td>{{ formatGermanDate(point.date) }}</td>
              <td><StatusBadge :status="point.tracking_status" /></td>
              <td class="number">{{ display(point.calories_kcal, ' kcal') }}</td>
              <td class="number">{{ display(point.deviation_kcal, ' kcal') }}</td>
              <td class="number">{{ display(point.protein_g, ' g') }}</td>
              <td class="number">{{ display(point.carbs_g, ' g') }}</td>
              <td class="number">{{ display(point.fat_g, ' g') }}</td>
            </tr>
            <tr v-if="!points.length"><td colspan="7" class="empty">{{ t('daily.noDays') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
