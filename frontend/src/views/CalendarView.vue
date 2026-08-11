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

import { api, ApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, formatGermanDayMonth, formatGermanWeekday } from '../date-format'
import type { DailyPoint } from '../types'

const now = new Date()
const currentMonth = new Date(now.getFullYear(), now.getMonth(), 1)
const selectedMonth = ref(new Date(currentMonth))
const days = ref<DailyPoint[]>([])
const error = ref('')
const loading = ref(true)
const labels: Record<string, string> = {
  under_budget: 'Im Budget',
  over_budget: 'Über Budget',
  above_maintenance: 'Über Budget und Erhaltungsbedarf',
  probably_incomplete: 'Kalorienwert fehlt',
  no_target: 'Kein Ziel festgelegt',
  no_data: 'Keine Daten',
}
const format = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const monthFormat = new Intl.DateTimeFormat('de-DE', { month: 'long', year: 'numeric' })

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
  if (day.target_kcal == null) return null
  const value = Number(day.target_kcal)
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
  return `${format.format(calories)} von ${format.format(budget)} kcal Tagesbudget`
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
    error.value =
      cause instanceof ApiError ? cause.message : 'Kalender konnte nicht geladen werden.'
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
    <div><h1>Kalender</h1><p>Kalorienbudget und Datenabdeckung Tag für Tag – ohne moralische Bewertung.</p></div>
    <div class="month-navigation" aria-label="Kalendermonat auswählen">
      <button type="button" aria-label="Vorheriger Monat" @click="changeMonth(-1)">
        <PhCaretLeft :size="17" weight="bold" />
      </button>
      <strong>{{ monthLabel }}</strong>
      <button
        type="button"
        aria-label="Nächster Monat"
        :disabled="isCurrentMonth"
        @click="changeMonth(1)"
      >
        <PhCaretRight :size="17" weight="bold" />
      </button>
    </div>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">Kalender wird geladen …</div>
  <template v-else>
    <section class="insight-strip" aria-label="Kalenderkennzahlen">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>Erfasste Tage</small><strong>{{ recordedDays.length }} von {{ days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhFire :size="20" weight="duotone" /></span>
        <span><small>Über Budget</small><strong>{{ overBudgetDays.length }} {{ overBudgetDays.length === 1 ? 'Tag' : 'Tage' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>Ø Kalorien</small><strong>{{ averageCalories == null ? '–' : `${format.format(averageCalories)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon red"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span>
          <small>Über Budget und Erhaltungsbedarf</small>
          <strong>{{ maintenanceConfigured ? `${aboveMaintenanceDays.length} ${aboveMaintenanceDays.length === 1 ? 'Tag' : 'Tage'}` : '–' }}</strong>
        </span>
      </article>
    </section>

    <section class="card calendar-card">
      <div class="section-card-header calendar-card-header">
        <div>
          <h2>Tagesübersicht</h2>
          <p>Grün liegt im Budget, Orange darüber und Rot über Budget und optionalem Erhaltungsbedarf.</p>
        </div>
        <div class="calendar-legend" aria-label="Kalenderlegende">
          <span><i class="under"></i>Im Budget</span>
          <span><i class="over"></i>Über Budget</span>
          <span><i class="maintenance"></i>Über Budget und Erhaltungsbedarf</span>
          <span><i class="missing"></i>Keine Daten</span>
        </div>
      </div>
      <div class="calendar-grid">
        <article
          v-for="day in days"
          :key="day.date"
          :class="['calendar-day', day.classification]"
          :aria-label="`${formatGermanDate(day.date)}: ${labels[day.classification ?? 'no_data']}`"
        >
          <div class="calendar-day-heading">
            <strong>{{ formatGermanWeekday(day.date) }}</strong>
            <time :datetime="day.date">{{ formatGermanDayMonth(day.date) }}</time>
          </div>
          <b>{{ calorieValue(day) == null ? '–' : format.format(calorieValue(day)!) }}<small v-if="calorieValue(day) != null"> kcal</small></b>
          <span>{{ labels[day.classification ?? 'no_data'] }}</span>
          <small v-if="day.target_kcal != null" class="calendar-day-reference">
            Budget {{ format.format(Number(day.target_kcal)) }}
            <template v-if="day.maintenance_kcal != null">
              · Erhaltung {{ format.format(Number(day.maintenance_kcal)) }}
            </template>
            kcal
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
        <div v-if="!days.length" class="empty">Für diesen Monat liegen keine Kalendertage vor.</div>
      </div>
    </section>
  </template>
</template>
