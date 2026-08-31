<script setup lang="ts">
import { PhCalendar } from '@phosphor-icons/vue'
import { ref, watch } from 'vue'

import { formatGermanDate, parseGermanDate } from '../date-format'
import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
const props = withDefaults(defineProps<{
  modelValue: string
  required?: boolean
  disabled?: boolean
  max?: string
  name?: string
  autocomplete?: string
}>(), {
  required: false,
  disabled: false,
  max: undefined,
  name: undefined,
  autocomplete: 'off',
})
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const textInput = ref<HTMLInputElement | null>(null)
const nativePicker = ref<HTMLInputElement | null>(null)
const displayValue = ref(formatGermanDate(props.modelValue))

function exceedsMax(value: string): boolean {
  return Boolean(props.max && value > props.max)
}

function setValidity(input: HTMLInputElement, value: string): void {
  input.setCustomValidity(exceedsMax(value) ? t('dateInput.max') : '')
}


watch(
  () => [props.modelValue, props.max] as const,
  ([value]) => {
    displayValue.value = formatGermanDate(value)
    if (textInput.value) setValidity(textInput.value, value)
  },
)
watch(
  () => i18n.global.locale.value,
  () => {
    displayValue.value = formatGermanDate(props.modelValue)
  },
)

function updateText(event: Event) {
  const input = event.target as HTMLInputElement
  displayValue.value = input.value
  if (!input.value && !props.required) {
    input.setCustomValidity('')
    emit('update:modelValue', '')
    return
  }
  const parsed = parseGermanDate(input.value)
  if (!parsed) {
    input.setCustomValidity(t('dateInput.invalid'))
    return
  }
  setValidity(input, parsed)
  emit('update:modelValue', parsed)
}

function normalizeText() {
  const parsed = parseGermanDate(displayValue.value)
  if (parsed) displayValue.value = formatGermanDate(parsed)
}

function chooseNativeDate(event: Event) {
  const value = (event.target as HTMLInputElement).value
  if (!value) return
  displayValue.value = formatGermanDate(value)
  setValidity(textInput.value ?? (event.target as HTMLInputElement), value)
  emit('update:modelValue', value)
}


function openPicker() {
  if (!nativePicker.value) return
  const picker = nativePicker.value as HTMLInputElement & { showPicker?: () => void }
  if (typeof picker.showPicker === 'function') picker.showPicker()
  else picker.click()
}

function reportValidity(): boolean {
  return textInput.value?.reportValidity() ?? true
}

defineExpose({ reportValidity })
</script>

<template>
  <span class="date-input">
    <input
      ref="textInput"
      :value="displayValue"
      type="text"
      inputmode="numeric"
      :placeholder="t('dateInput.placeholder')"
      :name="name"
      :autocomplete="autocomplete"
      :max="max"
      :required="required"
      :disabled="disabled"
      @input="updateText"
      @blur="normalizeText"
    />
    <button
      class="date-input-picker-button"
      type="button"
      :disabled="disabled"
      :aria-label="t('dateInput.choose')"
      @click="openPicker"
    ><PhCalendar :size="18" aria-hidden="true" /></button>
    <input
      ref="nativePicker"
      class="date-input-native-picker"
      type="date"
      :value="modelValue"
      :max="max"
      :disabled="disabled"
      tabindex="-1"
      aria-hidden="true"
      @change="chooseNativeDate"
    />
  </span>
</template>
