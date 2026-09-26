<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { isoDateInTimeZone, isoWeekday, shiftIsoDate } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import DateInput from './DateInput.vue'

const props = withDefaults(defineProps<{
  start: string
  end: string
  initialPreset?: string
  allowedPresets?: readonly string[]
}>(), {
  initialPreset: 'custom',
  allowedPresets: () => ['custom', 'week', 'last-week', 'month', '30', '90', '180', 'year'],
})
const emit = defineEmits<{ 'update:start': [value: string]; 'update:end': [value: string]; apply: [] }>()
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const startModel = computed({ get: () => props.start, set: (value) => emit('update:start', value) })
const endModel = computed({ get: () => props.end, set: (value) => emit('update:end', value) })
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const preset = ref(props.initialPreset ?? 'custom')

watch(
  () => props.initialPreset,
  (value) => { preset.value = value ?? 'custom' },
)

function apply() {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) return
  emit('apply')
}

function presetLabel(value: string): string {
  switch (value) {
    case 'custom': return t('dateFilter.custom')
    case 'week': return t('dateFilter.week')
    case 'last-week': return t('dateFilter.lastWeek')
    case 'month': return t('dateFilter.month')
    case '7': return t('dateFilter.last7')
    case '30': return t('dateFilter.last30')
    case '60': return t('dateFilter.last60')
    case '90': return t('dateFilter.last90')
    case '180': return t('dateFilter.last180')
    case 'year': return t('dateFilter.year')
    default: return value
  }
}

async function applyPreset() {
  const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  const weekday = isoWeekday(today) ?? 1
  let from = today
  let to = today
  if (preset.value === 'week') from = shiftIsoDate(today, -((weekday + 6) % 7))
  else if (preset.value === 'last-week') {
    to = shiftIsoDate(today, -((weekday + 6) % 7) - 1)
    from = shiftIsoDate(to, -6)
  } else if (preset.value === 'month') from = `${today.slice(0, 8)}01`
  else if (preset.value === '7') from = shiftIsoDate(today, -6)
  else if (preset.value === '30') from = shiftIsoDate(today, -29)
  else if (preset.value === '60') from = shiftIsoDate(today, -59)
  else if (preset.value === '90') from = shiftIsoDate(today, -89)
  else if (preset.value === '180') from = shiftIsoDate(today, -179)
  else if (preset.value === 'year') from = `${today.slice(0, 4)}-01-01`
  else return
  emit('update:start', from)
  emit('update:end', to)
  await nextTick()
  apply()
}
</script>

<template>
  <form class="filters" :aria-label="t('dateFilter.aria')" @submit.prevent="apply">
    <label class="field">
      {{ t('dateFilter.range') }}
      <select v-model="preset" @change="applyPreset">
        <option v-for="value in allowedPresets" :key="value" :value="value">
          {{ presetLabel(value) }}
        </option>
      </select>
    </label>
    <label class="field">{{ t('common.from') }} <DateInput ref="startInput" v-model="startModel" /></label>
    <label class="field">{{ t('common.to') }} <DateInput ref="endInput" v-model="endModel" /></label>
    <button class="button secondary compact-action compact-apply" type="submit">{{ t('common.apply') }}</button>
  </form>
</template>
