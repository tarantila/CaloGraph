<script setup lang="ts">
import {
  PhCalendarBlank,
  PhCaretLeft,
  PhCaretRight,
  PhChartBar,
  PhFire,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { hasActivityCredit } from '../activity'
import { api, localizeApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, formatGermanDayMonth, formatGermanWeekday } from '../date-format'
import { createDateFormatter, createNumberFormatter, i18n } from '../i18n'
import type { DailyPoint } from '../types'

const t = i18n.global.t.bind(i18n.global)

const now = new Date()
const currentMonth = new Date(now.getFullYear(), now.getMonth(), 1)
const selectedMonth = ref(new Date(currentMonth))
const days = ref<DailyPoint[]>([])
const error = ref('')
const loading = ref(true)
const classificationKeys: Record<string, string> = {
  under_budget: 'calendar.withinBudget',
  over_budget: 'calendar.overBudget',
  above_maintenance: 'calendar.overMaintenance',
  probably_incomplete: 'daily.withoutCalories',
  no_target: 'calendar.noTarget',
  no_data: 'calendar.noData',
}
function classificationLabel(value: string | undefined) {
  return t(classificationKeys[value ?? 'no_data'] ?? 'calendar.noData')
}

function dayUnit(count: number) {
  return count === 1 ? t('common.day') : t('common.days')
}
const format = createNumberFormatter({ maximumFractionDigits: 0 })
const monthFormat = createDateFormatter({ month: 'long', year: 'numeric' })

function dateString(value: Date) {
  const year = value.getFullYear()
  const month = `${value.getMonth() + 1}`.padStart(2, '0')
  const day = `${value.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

function calorieValue(day: DailyPoint) {
  if (day.calories_kcal == null) return null
  const value = Number(day.calories_kcal)
  return Number.isFinite(value) ? value : null
}

function budgetValue(day: DailyPoint) {
  const rawBudget = hasActivityCredit(day) ? day.effective_budget_kcal : day.target_kcal
  if (rawBudget == null) return null
  const value = Number(rawBudget)
  return Number.isFinite(value) && value > 0 ? value : null
}

function calorieProgress(day: DailyPoint) {
  const calories = calorieValue(day)
  const budget = budgetValue(day)
  if (calories == null || budget == null) return null
  return Math.min(Math.max(calories / budget, 0), 1)
}

function calorieProgressLabel(day: DailyPoint) {
  const calories = calorieValue(day)
  const budget = budgetValue(day)
  if (calories == null || budget == null) return ''
  const label = hasActivityCredit(day) ? t('activity.effectiveBudget') : t('charts.dailyBudget')
  return `${format.format(calories)} ${t('common.of')} ${format.format(budget)} kcal ${label}`
}

function monthRange() {
  const start = new Date(selectedMonth.value.getFullYear(), selectedMonth.value.getMonth(), 1)
  const isCurrent =
    start.getFullYear() === currentMonth.getFullYear() &&
    start.getMonth() === currentMonth.getMonth()
  const end = isCurrent
    ? now
    : new Date(start.getFullYear(), start.getMonth() + 1, 0)
  return { start: dateString(start), end: dateString(end) }
}

async function loadCalendar() {
  loading.value = true
  error.value = ''
  try {
    const range = monthRange()
    days.value = (
      await api<{ days: DailyPoint[] }>(
        `/analytics/calendar?start=${range.start}&end=${range.end}`,
      )
    ).days
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}

function changeMonth(offset: number) {
  selectedMonth.value = new Date(
    selectedMonth.value.getFullYear(),
    selectedMonth.value.getMonth() + offset,
    1,
  )
  void loadCalendar()
}

onMounted(loadCalendar)

const isCurrentMonth = computed(
  () =>
    selectedMonth.value.getFullYear() === currentMonth.getFullYear() &&
    selectedMonth.value.getMonth() === currentMonth.getMonth(),
)
const monthLabel = computed(() => monthFormat.format(selectedMonth.value))
const recordedDays = computed(() => days.value.filter((day) => calorieValue(day) != null))
const overBudgetDays = computed(() =>
  days.value.filter((day) =>
    ['over_budget', 'above_maintenance'].includes(day.classification ?? ''),
  ),
)
const aboveMaintenanceDays = computed(() =>
  days.value.filter((day) => day.classification === 'above_maintenance'),
)
const maintenanceConfigured = computed(() =>
  days.value.some((day) => day.maintenance_kcal != null),
)
const averageCalories = computed(() => {
  const values = recordedDays.value
    .map(calorieValue)
    .filter((value): value is number => value != null)
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
})
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('calendar.title') }}</h1><p>{{ t('calendar.description') }}</p></div>
    <div class="month-navigation" :aria-label="t('calendar.chooseMonth')">
      <button type="button" :aria-label="t('calendar.previous')" @click="changeMonth(-1)">
        <PhCaretLeft :size="17" weight="bold" />
      </button>
      <strong>{{ monthLabel }}</strong>
      <button
        type="button"
        :aria-label="t('calendar.next')"
        :disabled="isCurrentMonth"
        @click="changeMonth(1)"
      >
        <PhCaretRight :size="17" weight="bold" />
      </button>
    </div>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('calendar.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('calendar.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('calendar.recorded') }}</small><strong>{{ recordedDays.length }} {{ t('common.of') }} {{ days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhFire :size="20" weight="duotone" /></span>
        <span><small>{{ t('calendar.overBudget') }}</small><strong>{{ overBudgetDays.length }} {{ dayUnit(overBudgetDays.length) }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('calendar.averageCalories') }}</small><strong>{{ averageCalories == null ? '–' : format.format(averageCalories) + ' kcal' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon red"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span>
          <small>{{ t('calendar.overMaintenance') }}</small>
          <strong>{{ maintenanceConfigured ? `${aboveMaintenanceDays.length} ${dayUnit(aboveMaintenanceDays.length)}` : '–' }}</strong>
        </span>
      </article>
    </section>

    <section class="card calendar-card">
      <div class="section-card-header calendar-card-header">
        <div>
          <h2>{{ t('calendar.overview') }}</h2>
          <p>{{ t('calendar.legend') }}</p>
        </div>
        <div class="calendar-legend" :aria-label="t('calendar.legend')">
          <span><i class="under"></i>{{ t('calendar.withinBudget') }}</span>
          <span><i class="over"></i>{{ t('calendar.overBudget') }}</span>
          <span><i class="maintenance"></i>{{ t('calendar.overMaintenance') }}</span>
          <span><i class="missing"></i>{{ t('calendar.noData') }}</span>
        </div>
      </div>
      <div class="calendar-grid">
        <article
          v-for="day in days"
          :key="day.date"
          :class="['calendar-day', day.classification]"
          :aria-label="`${formatGermanDate(day.date)}: ${classificationLabel(day.classification)}`"
        >
          <div class="calendar-day-heading">
            <strong>{{ formatGermanWeekday(day.date) }}</strong>
            <time :datetime="day.date">{{ formatGermanDayMonth(day.date) }}</time>
          </div>
          <b>{{ calorieValue(day) == null ? '–' : format.format(calorieValue(day)!) }}<small v-if="calorieValue(day) != null"> kcal</small></b>
          <span>{{ classificationLabel(day.classification) }}</span>
          <small v-if="day.target_kcal != null" class="calendar-day-reference">
            {{ hasActivityCredit(day) ? t('activity.baseBudget') : t('charts.dailyBudget') }} {{ format.format(Number(day.target_kcal)) }}
            <template v-if="hasActivityCredit(day)">
              · {{ t('activity.activityCredit') }} +{{ format.format(Number(day.activity_credit_kcal)) }}
              · {{ t('activity.effectiveBudget') }} {{ day.effective_budget_kcal == null ? '–' : format.format(Number(day.effective_budget_kcal)) }}
            </template>
            kcal
          </small>
          <small v-if="hasActivityCredit(day)" class="calendar-day-reference">
            {{ t('activity.credited') }}
          </small>
          <StatusBadge v-if="day.tracking_status !== 'complete' && day.tracking_status !== 'no_data'" :status="day.tracking_status" />
          <progress
            v-if="calorieProgress(day) != null"
            class="calendar-calorie-progress"
            :value="calorieProgress(day)!"
            max="1"
            :aria-label="calorieProgressLabel(day)"
            :title="calorieProgressLabel(day)"
          >
            {{ format.format(calorieProgress(day)! * 100) }} %
          </progress>
        </article>
        <div v-if="!days.length" class="empty">{{ t('calendar.noDays') }}</div>
      </div>
    </section>
  </template>
</template>
