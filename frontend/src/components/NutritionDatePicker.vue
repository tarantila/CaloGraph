<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api, localizeApiError } from '../api'
import { calendarClassificationClass, calendarClassificationLabel } from '../calendar-status'
import { formatDate, isoDateInTimeZone } from '../date-format'
import { createDateFormatter, createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'

const props = defineProps<{ selectedDate: string; today: string }>()
const emit = defineEmits<{ select: [date: string] }>()
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const open = ref(false)
const month = ref(props.selectedDate.slice(0, 7))
const calendarToday = ref(props.today)
const days = ref<DailyPoint[]>([])
const loading = ref(false)
const error = ref('')
let latestRequestId = 0

const monthFormat = createDateFormatter({ month: 'long', year: 'numeric', timeZone: 'UTC' })
const weekdayFormat = createDateFormatter({ weekday: 'short', timeZone: 'UTC' })
const fullWeekdayFormat = createDateFormatter({ weekday: 'long', timeZone: 'UTC' })
const dayFormat = createDateFormatter({ dateStyle: 'full', timeZone: 'UTC' })
const number = createNumberFormatter({ maximumFractionDigits: 0 })
const monthStart = computed(() => new Date(`${month.value}-01T00:00:00Z`))
const monthLabel = computed(() => monthFormat.format(monthStart.value))
const weekdays = computed(() => Array.from({ length: 7 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 0, 5 + index))
  return { short: weekdayFormat.format(date).replace(/\.$/u, ''), full: fullWeekdayFormat.format(date) }
}))
const leadingDays = computed(() => (monthStart.value.getUTCDay() + 6) % 7)
const monthDays = computed(() => {
  const count = new Date(Date.UTC(monthStart.value.getUTCFullYear(), monthStart.value.getUTCMonth() + 1, 0)).getUTCDate()
  const byDate = new Map(days.value.map((day) => [day.date, day.classification]))
  return Array.from({ length: count }, (_, index) => {
    const date = `${month.value}-${String(index + 1).padStart(2, '0')}`
    const classification = date <= calendarToday.value ? byDate.get(date) : undefined
    const statusClass = calendarClassificationClass(classification)
    const status = statusClass ? calendarClassificationLabel(classification) : t('nutritionVerification.calendarUnavailable')
    const markers = [
      date === calendarToday.value ? t('overview.today') : '',
      date === props.selectedDate ? t('nutritionVerification.selectedDay') : '',
    ].filter(Boolean)
    return {
      date,
      number: number.format(index + 1),
      statusClass,
      label: [dayFormat.format(new Date(`${date}T00:00:00Z`)), status, ...markers].join(' · '),
    }
  })
})

async function loadCalendar(): Promise<void> {
  const requestId = ++latestRequestId
  const userId = auth.user?.id ?? null
  days.value = []
  error.value = ''
  loading.value = false
  if (!open.value || !userId) return
  const start = `${month.value}-01`
  const endOfMonth = new Date(Date.UTC(monthStart.value.getUTCFullYear(), monthStart.value.getUTCMonth() + 1, 0)).toISOString().slice(0, 10)
  const end = endOfMonth < calendarToday.value ? endOfMonth : calendarToday.value
  if (start > end) return
  loading.value = true
  try {
    const result = await api<{ days: DailyPoint[] }>(`/analytics/calendar?start=${start}&end=${end}`)
    if (requestId === latestRequestId && auth.user?.id === userId) days.value = result.days
  } catch (cause) {
    if (requestId === latestRequestId && auth.user?.id === userId) {
      error.value = localizeApiError(cause, 'nutritionVerification.calendarLoadFailed')
    }
  } finally {
    if (requestId === latestRequestId && auth.user?.id === userId) loading.value = false
  }
}

function closeCalendar(restoreFocus = false): void {
  open.value = false
  latestRequestId += 1
  loading.value = false
  if (restoreFocus) trigger.value?.focus()
}

async function toggleCalendar(): Promise<void> {
  if (open.value) {
    closeCalendar()
    return
  }
  calendarToday.value = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  month.value = props.selectedDate.slice(0, 7)
  open.value = true
  void loadCalendar()
  await nextTick()
  root.value?.querySelector<HTMLButtonElement>('.nutrition-calendar-day[aria-pressed="true"]')?.focus()
}

function changeMonth(offset: number): void {
  const nextMonth = new Date(monthStart.value)
  nextMonth.setUTCMonth(nextMonth.getUTCMonth() + offset)
  month.value = nextMonth.toISOString().slice(0, 7)
  void loadCalendar()
}

function chooseDate(date: string): void {
  emit('select', date)
  closeCalendar(true)
}

function handleOutside(event: Event): void {
  if (open.value && event.target instanceof Node && !root.value?.contains(event.target)) closeCalendar()
}

function handleKeydown(event: KeyboardEvent): void {
  if (open.value && event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    closeCalendar(true)
  }
}

watch(() => auth.user?.id ?? null, () => {
  latestRequestId += 1
  days.value = []
  error.value = ''
  loading.value = false
  calendarToday.value = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  if (open.value) void loadCalendar()
}, { flush: 'sync' })
watch(() => props.today, (today) => {
  calendarToday.value = today
  if (open.value) void loadCalendar()
})

onMounted(() => {
  document.addEventListener('pointerdown', handleOutside)
  document.addEventListener('focusin', handleOutside)
  document.addEventListener('keydown', handleKeydown)
})
onBeforeUnmount(() => {
  latestRequestId += 1
  document.removeEventListener('pointerdown', handleOutside)
  document.removeEventListener('focusin', handleOutside)
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div ref="root" class="nutrition-date-picker">
    <button
      ref="trigger"
      class="nutrition-date-trigger"
      type="button"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="open ? 'nutrition-date-calendar' : undefined"
      :aria-label="`${t('nutritionVerification.chooseDate')}: ${formatDate(selectedDate)}`"
      @click="toggleCalendar"
    >
      <strong>{{ formatDate(selectedDate) }}</strong>
    </button>
    <section
      v-if="open"
      id="nutrition-date-calendar"
      class="nutrition-calendar-popover"
      role="dialog"
      aria-modal="false"
      :aria-label="t('nutritionVerification.chooseDate')"
    >
      <div class="nutrition-calendar-month">
        <button type="button" :aria-label="t('calendar.previous')" @click="changeMonth(-1)"><span aria-hidden="true">‹</span></button>
        <span class="nutrition-calendar-month-label" aria-live="polite">{{ monthLabel }}</span>
        <button type="button" :aria-label="t('calendar.next')" @click="changeMonth(1)"><span aria-hidden="true">›</span></button>
      </div>
      <div class="nutrition-calendar-grid">
        <abbr v-for="weekday in weekdays" :key="weekday.full" class="nutrition-calendar-weekday" :title="weekday.full">{{ weekday.short }}</abbr>
        <span v-for="blank in leadingDays" :key="`blank-${blank}`" aria-hidden="true"></span>
        <button
          v-for="day in monthDays"
          :key="day.date"
          type="button"
          :class="['nutrition-calendar-day', day.statusClass]"
          :data-date="day.date"
          :aria-label="day.label"
          :title="day.label"
          :aria-pressed="day.date === selectedDate"
          :aria-current="day.date === calendarToday ? 'date' : undefined"
          @click="chooseDate(day.date)"
        >{{ day.number }}</button>
      </div>
      <p v-if="loading" class="nutrition-calendar-message" role="status">{{ t('calendar.loading') }}</p>
      <p v-else-if="error" class="nutrition-calendar-message error" role="alert">{{ error }}</p>
    </section>
  </div>
</template>

<style scoped>
.nutrition-date-picker { position: relative; min-width: 0; }
.nutrition-date-trigger { width: 100%; border-radius: .25rem; }
.nutrition-calendar-popover { position: absolute; top: calc(100% + .5rem); left: 50%; z-index: 20; width: 19rem; max-width: calc(100vw - 2rem); padding: .75rem; transform: translateX(-50%); border: 1px solid var(--border); border-radius: .75rem; background: var(--surface); box-shadow: 0 8px 28px color-mix(in srgb, var(--ink) 14%, transparent); }
.nutrition-calendar-month { display: grid; grid-template-columns: 34px minmax(0, 1fr) 34px; align-items: center; margin-bottom: .45rem; }
.nutrition-calendar-month-label { text-align: center; color: var(--ink); font-size: .8rem; font-weight: 600; }
.nutrition-calendar-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: .25rem; }
.nutrition-calendar-weekday { padding: .35rem 0; color: var(--muted-strong); text-align: center; text-decoration: none; font-size: .65rem; }
.nutrition-calendar-grid .nutrition-calendar-day { position: relative; min-width: 0; height: 34px; border: 1px solid var(--calendar-status-border, var(--border)); border-style: var(--calendar-status-border-style, solid); border-radius: .4rem; background: var(--calendar-status-background, transparent); color: var(--ink); font-size: .72rem; font-variant-numeric: tabular-nums; }
.nutrition-calendar-grid .nutrition-calendar-day:hover { box-shadow: inset 0 0 0 1px var(--border-strong); }
.nutrition-calendar-grid .nutrition-calendar-day[aria-pressed="true"] { outline: 2px solid var(--accent); outline-offset: 1px; font-weight: 700; }
.nutrition-calendar-day[aria-current="date"]::after { content: ''; position: absolute; bottom: 3px; left: calc(50% - 2px); width: 4px; height: 4px; border-radius: 50%; background: var(--ink); }
.nutrition-calendar-grid .nutrition-calendar-day:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 1px; z-index: 1; }
.nutrition-calendar-message { margin: .7rem 0 0; font-size: .7rem; color: var(--muted-strong); }
</style>
