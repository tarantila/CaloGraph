<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

import { isoDateInTimeZone, isoWeekday, shiftIsoDate } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import DateInput from './DateInput.vue'

const props = defineProps<{ start: string; end: string }>()
const emit = defineEmits<{ 'update:start': [value: string]; 'update:end': [value: string]; apply: [] }>()
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const startModel = computed({ get: () => props.start, set: (value) => emit('update:start', value) })
const endModel = computed({ get: () => props.end, set: (value) => emit('update:end', value) })
const startInput = ref<InstanceType<typeof DateInput> | null>(null)
const endInput = ref<InstanceType<typeof DateInput> | null>(null)
const preset = ref('custom')

function apply() {
  if (!startInput.value?.reportValidity() || !endInput.value?.reportValidity()) return
  emit('apply')
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
  else if (preset.value === '30') from = shiftIsoDate(today, -29)
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
        <option value="custom">{{ t('dateFilter.custom') }}</option>
        <option value="week">{{ t('dateFilter.week') }}</option>
        <option value="last-week">{{ t('dateFilter.lastWeek') }}</option>
        <option value="month">{{ t('dateFilter.month') }}</option>
        <option value="30">{{ t('dateFilter.last30') }}</option>
        <option value="90">{{ t('dateFilter.last90') }}</option>
        <option value="180">{{ t('dateFilter.last180') }}</option>
        <option value="year">{{ t('dateFilter.year') }}</option>
      </select>
    </label>
    <label class="field">{{ t('common.from') }} <DateInput ref="startInput" v-model="startModel" /></label>
    <label class="field">{{ t('common.to') }} <DateInput ref="endInput" v-model="endModel" /></label>
    <button class="button secondary" type="submit">{{ t('common.apply') }}</button>
  </form>
</template>
