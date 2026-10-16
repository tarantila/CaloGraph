<script setup lang="ts">
import { PhCalendar } from '@phosphor-icons/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

import {
  rangeForDays, supportedPeriodPresets, validPeriodRange,
  type AppliedPeriod, type PeriodLimits, type PeriodPreset, type PeriodRange, type PeriodSelection,
} from '../analytics-period'
import { formatGermanDate } from '../date-format'
import { i18n } from '../i18n'
import type { ReaderPeriodCapabilities } from '../types'
import DateInput from './DateInput.vue'

const props = defineProps<{
  start: string
  end: string
  today: string
  period: PeriodSelection
  minDays: number
  maxDays: number
  customRange: boolean
  maxDate?: string
  periodCapabilities?: ReaderPeriodCapabilities | null
  allRange: PeriodRange | null
  additionalPresets?: readonly PeriodPreset[]
}>()
const emit = defineEmits<{ apply: [value: AppliedPeriod] }>()

const t = i18n.global.t.bind(i18n.global)
const id = useId()
const popoverId = `${id}-period-popover`
const limitId = `${id}-period-limit`
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const focusReturn = ref<HTMLButtonElement | null>(null)
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const isOpen = ref(false)
const draftStart = ref(props.start)
const draftEnd = ref(props.end)
const validationError = ref('')
const limits = computed<PeriodLimits>(() => ({
  minDays: props.minDays, maxDays: props.maxDays,
  customRange: props.customRange, maxDate: props.maxDate,
}))
const checkedAllRange = computed(() => {
  const value = props.allRange
  return props.periodCapabilities?.supports_all_data === true
    && props.periodCapabilities.all_data_start_date === value?.start
    && value && validPeriodRange(value, limits.value) ? value : null
})
const options = computed(() => supportedPeriodPresets(limits.value, checkedAllRange.value, props.additionalPresets))
const desktopPresets = ['7', '30', '90'] as const

function presetLabel(preset: PeriodPreset): string {
  if (preset === 'all') return t('analysisV2.periodAll')
  if (preset === '3661') return t('analysisV2.periodMax')
  return t(preset === '365' ? 'analysisV2.periodYear' : `analysisV2.period${preset}`)
}

const dateSummary = computed(() => `${formatGermanDate(props.start)} – ${formatGermanDate(props.end)}`)
const triggerText = computed(() => props.period === 'custom'
  ? `${t('analysisV2.customRange')}: ${dateSummary.value}`
  : presetLabel(props.period))
const triggerLabel = computed(() => props.period === 'custom'
  ? t('analysisV2.customRangeSummary', {
      start: formatGermanDate(props.start), end: formatGermanDate(props.end),
    })
  : `${t('analysisV2.period')}: ${triggerText.value}`)

function close(restoreFocus = false): void {
  isOpen.value = false
  validationError.value = ''
  if (restoreFocus) void nextTick(() => (focusReturn.value ?? trigger.value)?.focus())
}

function onDocumentPointerDown(event: PointerEvent): void {
  const target = event.target as Node | null
  if (root.value && target && !root.value.contains(target)) close()
}

function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && isOpen.value) {
    event.preventDefault()
    close(true)
  }
}

function onFocusOut(event: FocusEvent): void {
  if (event.relatedTarget instanceof Node && root.value && !root.value.contains(event.relatedTarget)) close()
}

async function open(opener: HTMLButtonElement | null): Promise<void> {
  focusReturn.value = opener
  draftStart.value = props.start
  draftEnd.value = props.end
  validationError.value = ''
  isOpen.value = true
  await nextTick()
  if (!isOpen.value) return
  const dateField = root.value?.querySelector<HTMLInputElement>('.date-input input[type="text"]')
  const quickOption = root.value?.querySelector<HTMLButtonElement>('.analysis-period-option')
  if (props.period === 'custom' && dateField) dateField.focus()
  else (quickOption ?? dateField)?.focus()
}

function toggle(event: MouseEvent): void {
  if (isOpen.value) close()
  else void open(event.currentTarget as HTMLButtonElement)
}

function selectPreset(preset: PeriodPreset, fromPopover = false): void {
  if (!options.value.includes(preset)) return
  const range = preset === 'all' ? checkedAllRange.value : rangeForDays(props.today, Number(preset))
  if (!range || !validPeriodRange(range, limits.value)) return
  emit('apply', { ...range, period: preset })
  close(fromPopover)
}

function applyCustom(): void {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) {
    validationError.value = t('analysisV2.invalidRange')
    return
  }
  const range = { start: draftStart.value, end: draftEnd.value }
  if (!props.customRange || !validPeriodRange(range, limits.value)) {
    validationError.value = t('analysisV2.periodRangeError', { days: props.maxDays })
    return
  }
  emit('apply', { ...range, period: 'custom' })
  close(true)
}

watch(() => [props.start, props.end] as const, ([start, end]) => {
  if (!isOpen.value) {
    draftStart.value = start
    draftEnd.value = end
  }
})

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onDocumentKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div ref="root" class="analysis-period-picker" @focusout="onFocusOut">
    <div class="analysis-period-strip" :aria-label="t('analysisV2.period')">
      <button
        v-for="preset in desktopPresets"
        :key="preset"
        class="analysis-period-quick-button"
        type="button"
        :data-period="preset"
        :disabled="!options.includes(preset)"
        :aria-pressed="period === preset"
        :aria-describedby="!options.includes(preset) ? limitId : undefined"
        @click="selectPreset(preset)"
      >{{ presetLabel(preset) }}</button>
      <button
        class="analysis-period-custom-button"
        type="button"
        :disabled="!customRange"
        :aria-describedby="!customRange ? limitId : undefined"
        :aria-pressed="period === 'custom' || period === '180' || period === '365' || period === 'all' || (props.additionalPresets?.includes(period as PeriodPreset) ?? false)"
        :aria-expanded="isOpen"
        :aria-controls="popoverId"
        @click="toggle"
      >
        <span>{{ t('analysisV2.customRange') }}</span>
        <PhCalendar :size="14" aria-hidden="true" />
      </button>
    </div>
    <p
      v-if="desktopPresets.some((preset) => !options.includes(preset)) || !customRange"
      :id="limitId"
      class="analysis-period-reader-limit"
    >{{ t('analysisV2.readerRangeLimit', { min: minDays, max: maxDays }) }}</p>
    <button
      ref="trigger"
      class="analysis-period-trigger"
      type="button"
      :aria-label="triggerLabel"
      :aria-controls="popoverId"
      :aria-expanded="isOpen"
      @click="toggle"
    >
      <span>{{ triggerText }}</span>
      <span class="analysis-period-chevron" aria-hidden="true">▾</span>
    </button>
    <div
      v-if="isOpen"
      :id="popoverId"
      class="analysis-period-popover"
      :aria-label="t('analysisV2.period')"
    >
      <div
        class="analysis-period-layout"
        :class="{ 'analysis-period-layout--quick-only': !customRange }"
      >
        <section v-if="customRange" class="analysis-period-panel" :aria-label="t('analysisV2.customRange')">
          <h2 class="analysis-period-panel-title">{{ t('analysisV2.customRange') }}</h2>
          <form class="analysis-period-custom-form" @submit.prevent="applyCustom">
            <label>{{ t('analysisV2.startDate') }}
              <DateInput ref="startInput" v-model="draftStart" required :max="maxDate" />
            </label>
            <label>{{ t('analysisV2.endDate') }}
              <DateInput ref="endInput" v-model="draftEnd" required :max="maxDate" />
            </label>
            <p v-if="validationError" class="analysis-period-error" role="alert">{{ validationError }}</p>
            <button class="button secondary" type="submit">{{ t('analysisV2.applyRange') }}</button>
          </form>
        </section>
        <section class="analysis-period-panel analysis-period-quick" :aria-label="t('analysisV2.quickPeriods')">
          <h2 class="analysis-period-panel-title">{{ t('analysisV2.quickPeriods') }}</h2>
          <div class="analysis-period-options">
            <button
              v-for="preset in options"
              :key="preset"
              class="analysis-period-option"
              type="button"
              :data-period="preset"
              :aria-pressed="period === preset"
              @click="selectPreset(preset, true)"
            >
              {{ presetLabel(preset) }}
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.analysis-period-picker { position: relative; display: inline-flex; align-items: center; gap: .5rem; min-width: 0; flex: 0 1 auto; flex-wrap: wrap; max-width: 100%; }
.analysis-period-strip { display: inline-flex; align-items: center; flex-wrap: nowrap; width: fit-content; max-width: 100%; min-width: 0; height: var(--control-height); padding: 2px; border: 1px solid var(--border); border-radius: .55rem; background: var(--control-bg); }
.analysis-period-strip button { display: inline-flex; align-items: center; justify-content: center; gap: .35rem; height: 100%; min-width: 72px; border: 0; border-radius: .45rem; padding: .5rem .7rem; background: transparent; color: var(--muted-strong); font: inherit; font-size: .72rem; font-weight: 600; line-height: 1.5; white-space: nowrap; cursor: pointer; }
.analysis-period-strip button:not([aria-pressed="true"]):hover:not(:disabled) { color: var(--ink); }
.analysis-period-strip button[aria-pressed="true"] { background: var(--accent); color: #fff; }
.analysis-period-strip button:disabled { cursor: not-allowed; opacity: .6; }
.analysis-period-strip button:disabled:hover { background: transparent; color: var(--muted-strong); }
.analysis-period-reader-limit { flex-basis: 100%; margin: 0; color: var(--muted-strong); font-size: .8rem; }
.analysis-period-trigger { display: none; align-items: center; justify-content: space-between; gap: .65rem; min-height: 2.75rem; max-width: 100%; padding: .55rem .8rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--control-bg); color: var(--ink); font: inherit; font-weight: 600; cursor: pointer; }
.analysis-period-trigger > span:first-child { min-width: 0; overflow-wrap: anywhere; white-space: normal; }
.analysis-period-trigger:hover, .analysis-period-trigger[aria-expanded="true"] { border-color: var(--accent); }
.analysis-period-strip button:focus-visible, .analysis-period-trigger:focus-visible, .analysis-period-popover button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.analysis-period-chevron { color: var(--muted); }
.analysis-period-popover { position: absolute; z-index: 20; top: calc(100% + .45rem); right: 0; width: min(36rem, calc(100vw - 2rem)); max-height: min(80vh, 36rem); overflow-y: auto; padding: .8rem; border: 1px solid var(--border); border-radius: .8rem; background: var(--popover); box-shadow: var(--shadow-popover); }
.analysis-period-layout { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(10rem, .9fr); gap: 1rem; }
.analysis-period-layout--quick-only { grid-template-columns: 1fr; }
.analysis-period-panel { display: grid; align-content: start; gap: .65rem; min-width: 0; }
.analysis-period-panel-title { margin: 0; color: var(--muted-strong); font-size: .88rem; font-weight: 650; }
.analysis-period-options { display: grid; gap: .2rem; }
.analysis-period-popover button:not(.button) { min-height: 2.75rem; padding: .55rem .7rem; border: 0; border-radius: .5rem; background: transparent; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.analysis-period-popover button:not(.button):hover, .analysis-period-popover button[aria-pressed="true"] { background: color-mix(in srgb, var(--accent) 12%, var(--popover)); color: var(--accent-hover); }
.analysis-period-custom-form { display: grid; gap: .65rem; }
.analysis-period-custom-form label { display: grid; gap: .3rem; color: var(--muted-strong); font-size: .9rem; }
.analysis-period-custom-form .date-input { width: 100%; }
:deep(.analysis-period-custom-form .date-input-native-picker) { position: absolute; right: 0; bottom: 0; width: 1px; height: 1px; min-height: 0; padding: 0; border: 0; opacity: 0; pointer-events: none; }
.analysis-period-custom-form > button { justify-self: start; min-height: 2.75rem; }
.analysis-period-error { margin: 0; color: var(--danger); font-size: .9rem; }
@media (min-width: 601px) and (max-width: 800px) {
  .analysis-period-popover { left: 0; right: auto; }
}
@media (max-width: 600px) {
  .analysis-period-picker { width: 100%; }
  .analysis-period-strip { display: none; }
  .analysis-period-reader-limit { display: none; }
  .analysis-period-trigger { display: inline-flex; width: 100%; }
  .analysis-period-popover { left: 0; right: auto; width: 100%; box-sizing: border-box; max-width: 100%; }
  .analysis-period-layout { grid-template-columns: 1fr; gap: 1.1rem; }
}
</style>
