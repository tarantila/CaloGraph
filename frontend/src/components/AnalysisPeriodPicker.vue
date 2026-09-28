<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { analysisRangeWithinDays, validAnalyticsRange } from '../analytics-period'
import { analysisRangeForDays } from '../analysis-v2'
import { formatGermanDate } from '../date-format'
import { i18n } from '../i18n'
import DateInput from './DateInput.vue'

type Preset = '7' | '30' | '90' | '365'
type Selection = Preset | 'custom'

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
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const isOpen = ref(false)
const editingCustom = ref(false)
const draftStart = ref(props.start)
const draftEnd = ref(props.end)
const validationError = ref('')

const triggerText = computed(() => props.period === 'custom'
  ? `${formatGermanDate(props.start)} – ${formatGermanDate(props.end)}`
  : t(props.period === '365' ? 'analysisV2.periodYear' : `analysisV2.period${props.period}`))
const triggerLabel = computed(() => props.period === 'custom'
  ? t('analysisV2.customRangeSummary', { start: formatGermanDate(props.start), end: formatGermanDate(props.end) })
  : `${t('analysisV2.period')}: ${triggerText.value}`)

function close(restoreFocus = false): void {
  isOpen.value = false
  validationError.value = ''
  if (restoreFocus) void nextTick(() => trigger.value?.focus())
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

async function open(): Promise<void> {
  draftStart.value = props.start
  draftEnd.value = props.end
  editingCustom.value = props.period === 'custom'
  validationError.value = ''
  isOpen.value = true
  await nextTick()
  if (editingCustom.value) {
    root.value?.querySelector<HTMLInputElement>('.date-input input[type="text"]')?.focus()
  } else {
    root.value?.querySelector<HTMLButtonElement>('.analysis-period-option')?.focus()
  }
}

function toggle(): void {
  if (isOpen.value) close()
  else void open()
}

function selectPreset(preset: Preset): void {
  const range = analysisRangeForDays(props.today, Number(preset))
  emit('apply', { ...range, period: preset })
  close(true)
}

async function editCustom(): Promise<void> {
  if (!props.customRange) return
  editingCustom.value = true
  validationError.value = ''
  await nextTick()
  root.value?.querySelector<HTMLInputElement>('.date-input input[type="text"]')?.focus()
}

function applyCustom(): void {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) return
  if (!validAnalyticsRange(draftStart.value, draftEnd.value)) {
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
      <div class="analysis-period-options" :aria-label="t('analysisV2.quickPeriods')">
        <button
          v-for="preset in presets"
          :key="preset"
          class="analysis-period-option"
          type="button"
          :aria-pressed="period === preset"
          @click="selectPreset(preset)"
        >
          {{ t(preset === '365' ? 'analysisV2.periodYear' : `analysisV2.period${preset}`) }}
        </button>
      </div>
      <template v-if="customRange">
        <div class="analysis-period-divider" role="separator"></div>
        <button
          class="analysis-period-custom-choice"
          type="button"
          :aria-pressed="editingCustom"
          @click="editCustom"
        >
          {{ t('analysisV2.customRange') }}
        </button>
        <form v-if="editingCustom" class="analysis-period-custom-form" @submit.prevent="applyCustom">
          <label>{{ t('analysisV2.startDate') }}
            <DateInput ref="startInput" v-model="draftStart" required :max="today" />
          </label>
          <label>{{ t('analysisV2.endDate') }}
            <DateInput ref="endInput" v-model="draftEnd" required :max="today" />
          </label>
          <p v-if="validationError" class="analysis-period-error" role="alert">{{ validationError }}</p>
          <button class="button secondary" type="submit">{{ t('analysisV2.applyRange') }}</button>
        </form>
      </template>
    </div>
  </div>
</template>

<style scoped>
.analysis-period-picker { position: relative; display: inline-flex; flex: 0 0 auto; }
.analysis-period-trigger { display: inline-flex; align-items: center; justify-content: space-between; gap: .65rem; min-height: 2.75rem; max-width: 100%; padding: .55rem .8rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--control-bg); color: var(--ink); font: inherit; font-weight: 600; cursor: pointer; }
.analysis-period-trigger:hover, .analysis-period-trigger[aria-expanded="true"] { border-color: var(--accent); }
.analysis-period-trigger:focus-visible, .analysis-period-popover button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.analysis-period-chevron { color: var(--muted); }
.analysis-period-popover { position: absolute; z-index: 20; top: calc(100% + .45rem); right: 0; display: grid; gap: .45rem; width: min(22rem, calc(100vw - 2rem)); max-height: min(80vh, 36rem); overflow-y: auto; padding: .6rem; border: 1px solid var(--border); border-radius: .75rem; background: var(--popover); box-shadow: var(--shadow-popover); }
.analysis-period-options { display: grid; gap: .25rem; }
.analysis-period-popover button:not(.button) { min-height: 2.6rem; padding: .5rem .65rem; border: 0; border-radius: .45rem; background: transparent; color: var(--ink); font: inherit; text-align: left; cursor: pointer; }
.analysis-period-popover button:not(.button):hover, .analysis-period-popover button[aria-pressed="true"] { background: color-mix(in srgb, var(--accent) 12%, var(--popover)); color: var(--accent-hover); }
.analysis-period-divider { height: 1px; background: var(--border); }
.analysis-period-custom-form { display: grid; gap: .65rem; padding: .15rem .25rem .25rem; }
.analysis-period-custom-form label { display: grid; gap: .3rem; color: var(--muted-strong); font-size: .9rem; }
.analysis-period-custom-form .date-input { width: 100%; }
:deep(.analysis-period-custom-form .date-input-native-picker) { position: absolute; right: 0; bottom: 0; width: 1px; height: 1px; min-height: 0; padding: 0; border: 0; opacity: 0; pointer-events: none; }
.analysis-period-custom-form > button { justify-self: end; }
.analysis-period-error { margin: 0; color: var(--danger); font-size: .9rem; }
@media (max-width: 600px) {
  .analysis-period-picker { width: 100%; }
  .analysis-period-trigger { width: 100%; }
  .analysis-period-popover { left: 0; right: auto; width: 100%; }
}
</style>
