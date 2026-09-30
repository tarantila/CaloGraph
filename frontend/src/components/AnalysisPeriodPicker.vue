<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { analysisRangeWithinDays, validAnalyticsRange } from '../analytics-period'
import { analysisRangeForDays, type AnalysisHubPeriod, type AnalysisPeriodSelection } from '../analysis-v2'
import { formatGermanDate } from '../date-format'
import { i18n } from '../i18n'
import DateInput from './DateInput.vue'

type Preset = AnalysisHubPeriod
type Selection = AnalysisPeriodSelection

const props = defineProps<{
  start: string
  end: string
  today: string
  period: Selection
  presets: readonly Preset[]
  customRange: boolean
  maxDays: number
}>()
const emit = defineEmits<{
  apply: [range: { start: string; end: string; period: Selection }]
}>()

const t = i18n.global.t.bind(i18n.global)
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const focusReturn = ref<HTMLButtonElement | null>(null)
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const isOpen = ref(false)
const draftStart = ref(props.start)
const draftEnd = ref(props.end)
const validationError = ref('')

function presetLabel(preset: Preset): string {
  if (preset === 'all') return t('analysisV2.periodAll')
  return t(preset === '365' ? 'analysisV2.periodYear' : `analysisV2.period${preset}`)
}

const triggerText = computed(() => props.period === 'custom'
  ? `${formatGermanDate(props.start)} – ${formatGermanDate(props.end)}`
  : presetLabel(props.period))
const triggerLabel = computed(() => props.period === 'custom'
  ? t('analysisV2.customRangeSummary', { start: formatGermanDate(props.start), end: formatGermanDate(props.end) })
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

async function open(opener: HTMLButtonElement | null): Promise<void> {
  focusReturn.value = opener
  draftStart.value = props.start
  draftEnd.value = props.end
  validationError.value = ''
  isOpen.value = true
  await nextTick()
  if (props.period === 'custom') {
    root.value?.querySelector<HTMLInputElement>('.date-input input[type="text"]')?.focus()
  } else {
    root.value?.querySelector<HTMLButtonElement>('.analysis-period-option')?.focus()
  }
}

function toggle(event: MouseEvent): void {
  if (isOpen.value) close()
  else void open(event.currentTarget as HTMLButtonElement)
}

function selectPreset(preset: Preset, fromPopover = false): void {
  const days = preset === 'all' ? props.maxDays : Number(preset)
  const range = analysisRangeForDays(props.today, days)
  emit('apply', { ...range, period: preset })
  close(fromPopover)
}

function applyCustom(): void {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) {
    validationError.value = t('analysisV2.invalidRange')
    return
  }
  if (!validAnalyticsRange(draftStart.value, draftEnd.value) || draftEnd.value > props.today) {
    validationError.value = t('analysisV2.invalidRange')
    return
  }
  if (!analysisRangeWithinDays(draftStart.value, draftEnd.value, props.maxDays)) {
    validationError.value = t('analysisV2.periodRangeError', { days: props.maxDays })
    return
  }
  emit('apply', { start: draftStart.value, end: draftEnd.value, period: 'custom' })
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
  <div ref="root" class="analysis-period-picker">
    <div class="analysis-period-strip" :aria-label="t('analysisV2.period')">
      <button
        v-for="preset in presets"
        :key="preset"
        class="analysis-period-quick-button"
        type="button"
        :aria-pressed="period === preset"
        @click="selectPreset(preset)"
      >{{ presetLabel(preset) }}</button>
      <button
        v-if="customRange"
        class="analysis-period-custom-button"
        type="button"
        :aria-pressed="period === 'custom' ? true : undefined"
        :aria-expanded="isOpen"
        aria-controls="analysis-period-popover"
        @click="toggle"
      >{{ t('analysisV2.customRange') }}<span v-if="period === 'custom'">: {{ triggerText }}</span></button>
    </div>
    <button
      ref="trigger"
      class="analysis-period-trigger"
      type="button"
      :aria-label="triggerLabel"
      aria-controls="analysis-period-popover"
      :aria-expanded="isOpen"
      @click="toggle"
    >
      <span>{{ triggerText }}</span>
      <span class="analysis-period-chevron" aria-hidden="true">▾</span>
    </button>
    <div
      v-if="isOpen"
      id="analysis-period-popover"
      class="analysis-period-popover"
      :aria-label="t('analysisV2.period')"
      @focusout="(event) => { if (!event.relatedTarget || !root?.contains(event.relatedTarget as Node)) close() }"
    >
      <div
        class="analysis-period-layout"
        :class="{ 'analysis-period-layout--quick-only': !customRange }"
      >
        <section v-if="customRange" class="analysis-period-panel" :aria-label="t('analysisV2.customRange')">
          <h2 class="analysis-period-panel-title">{{ t('analysisV2.customRange') }}</h2>
          <form class="analysis-period-custom-form" @submit.prevent="applyCustom">
            <label>{{ t('analysisV2.startDate') }}
              <DateInput ref="startInput" v-model="draftStart" required :max="today" />
            </label>
            <label>{{ t('analysisV2.endDate') }}
              <DateInput ref="endInput" v-model="draftEnd" required :max="today" />
            </label>
            <p v-if="validationError" class="analysis-period-error" role="alert">{{ validationError }}</p>
            <button class="button secondary" type="submit">{{ t('analysisV2.applyRange') }}</button>
          </form>
        </section>
        <section class="analysis-period-panel analysis-period-quick" :aria-label="t('analysisV2.quickPeriods')">
          <h2 class="analysis-period-panel-title">{{ t('analysisV2.quickPeriods') }}</h2>
          <div class="analysis-period-options">
            <button
              v-for="preset in presets"
              :key="preset"
              class="analysis-period-option"
              type="button"
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
.analysis-period-picker { position: relative; display: inline-flex; align-items: center; gap: .5rem; min-width: 0; flex: 0 1 auto; }
.analysis-period-strip { display: flex; align-items: center; flex-wrap: wrap; gap: .3rem; min-width: 0; }
.analysis-period-strip button { min-height: 2.75rem; padding: .5rem .7rem; border: 1px solid var(--border); border-radius: .5rem; background: var(--control-bg); color: var(--ink); font: inherit; font-weight: 600; cursor: pointer; }
.analysis-period-strip button:hover, .analysis-period-strip button[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--control-bg)); }
.analysis-period-strip button[aria-pressed="true"] { box-shadow: inset 0 -3px var(--accent); }
.analysis-period-trigger { display: none; align-items: center; justify-content: space-between; gap: .65rem; min-height: 2.75rem; max-width: 100%; padding: .55rem .8rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--control-bg); color: var(--ink); font: inherit; font-weight: 600; cursor: pointer; }
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
@media (max-width: 600px) {
  .analysis-period-picker { width: 100%; }
  .analysis-period-strip { display: none; }
  .analysis-period-trigger { display: inline-flex; width: 100%; }
  .analysis-period-popover { left: 0; right: auto; width: 100%; }
  .analysis-period-layout { grid-template-columns: 1fr; gap: 1.1rem; }
}
</style>
