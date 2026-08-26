<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { api, localizeApiError } from '../api'
import { type AnalyticsCompactPreset } from '../analytics-period'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import DateFilter from './DateFilter.vue'
import DateInput from './DateInput.vue'

const props = withDefaults(defineProps<{
  start: string
  end: string
  initialPreset?: string
  compactPreset?: AnalyticsCompactPreset | null
  compactPresets?: AnalyticsCompactPreset[]
}>(), {
  initialPreset: 'custom',
  compactPreset: null,
  compactPresets: () => ['7', '30', '60', 'all'],
})

const emit = defineEmits<{
  'update:start': [value: string]
  'update:end': [value: string]
  apply: []
  preset: [value: AnalyticsCompactPreset]
}>()

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const startModel = computed({ get: () => props.start, set: (value) => emit('update:start', value) })
const MAX_ANALYTICS_RANGE_DAYS = 3660
const endModel = computed({ get: () => props.end, set: (value) => emit('update:end', value) })
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const compactOptions = computed(() => props.compactPresets.filter((value) => value !== 'custom'))

function inferPresetFromOptions(start: string, end: string, options: AnalyticsCompactPreset[]): AnalyticsCompactPreset {
  for (const value of options) {
    if (value === 'all') continue
    const expectedStart = value === 'year'
      ? `${end.slice(0, 4)}-01-01`
      : shiftIsoDate(end, -(Number(value) - 1))
    if (start === expectedStart) return value
  }
  return 'custom'
}

const activePreset = ref<AnalyticsCompactPreset>(
  props.compactPreset ?? inferPresetFromOptions(props.start, props.end, props.compactPresets),
)
const compactError = ref('')
const compactLoading = ref(false)

watch(
  () => props.compactPreset,
  (value) => {
    activePreset.value = value ?? inferPreset(props.start, props.end)
  },
)

function inferPreset(start: string, end: string): AnalyticsCompactPreset {
  return inferPresetFromOptions(start, end, compactOptions.value)
}

function presetLabel(value: AnalyticsCompactPreset): string {
  if (value === '7') return t('overviewUi.period7')
  if (value === '30') return t('overviewUi.period30')
  if (value === '60') return t('overviewUi.period60')
  if (value === '90') return t('dateFilter.last90')
  if (value === '180') return t('dateFilter.last180')
  if (value === 'year') return t('dateFilter.year')
  return t('overviewUi.periodAll')
}

function rangeForPreset(value: Exclude<AnalyticsCompactPreset, 'custom' | 'all'>, today: string) {
  if (value === 'year') return { start: `${today.slice(0, 4)}-01-01`, end: today }
  return { start: shiftIsoDate(today, -(Number(value) - 1)), end: today }
}

async function applyCompactPreset(value: AnalyticsCompactPreset) {
  activePreset.value = value
  compactError.value = ''
  emit('preset', value)
  if (value === 'custom') return

  compactLoading.value = true
  try {
    const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
    const earliestSupported = shiftIsoDate(today, -(MAX_ANALYTICS_RANGE_DAYS - 1))
    const range = value === 'all'
      ? await api<{ data_start_date: string | null }>('/dashboard/summary').then((summary) => ({
        start: summary.data_start_date && summary.data_start_date > earliestSupported
          ? summary.data_start_date
          : earliestSupported,
        end: today,
      }))
      : rangeForPreset(value, today)
    emit('update:start', range.start)
    emit('update:end', range.end)
    await nextTick()
    emit('apply')
  } catch (cause) {
    compactError.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    compactLoading.value = false
  }
}

function applyCompactCustom() {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) return
  activePreset.value = 'custom'
  emit('preset', 'custom')
  emit('apply')
}

function applyDesktop() {
  activePreset.value = inferPreset(props.start, props.end)
  emit('preset', activePreset.value)
  emit('apply')
}
</script>

<template>
  <div class="analytics-period-filter" role="region" :aria-label="t('dateFilter.aria')">
    <div class="analytics-period-filter-desktop">
      <DateFilter
        :start="start"
        :end="end"
        :initial-preset="initialPreset"
        @update:start="emit('update:start', $event)"
        @update:end="emit('update:end', $event)"
        @apply="applyDesktop"
      />
    </div>

    <div class="analytics-period-filter-compact">
      <div class="analytics-period-presets" role="group" :aria-label="t('dateFilter.range')">
        <button
          v-for="value in compactOptions"
          :key="value"
          class="analytics-period-button"
          type="button"
          :aria-pressed="activePreset === value"
          :disabled="compactLoading"
          @click="applyCompactPreset(value)"
        >
          {{ presetLabel(value) }}
        </button>
        <button
          class="analytics-period-button"
          type="button"
          :aria-pressed="activePreset === 'custom'"
          :disabled="compactLoading"
          @click="applyCompactPreset('custom')"
        >
          {{ t('dateFilter.individual') }}
        </button>
      </div>
      <form v-if="activePreset === 'custom'" class="analytics-period-custom-fields" @submit.prevent="applyCompactCustom">
        <label class="field">{{ t('common.from') }} <DateInput ref="startInput" v-model="startModel" required /></label>
        <label class="field">{{ t('common.to') }} <DateInput ref="endInput" v-model="endModel" required /></label>
        <button class="button secondary compact-action compact-apply" type="submit">{{ t('common.apply') }}</button>
      </form>
      <p v-if="compactError" class="analytics-period-error" role="alert">{{ compactError }}</p>
    </div>
  </div>
</template>
