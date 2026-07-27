<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

const props = defineProps<{ start: string; end: string }>()
const emit = defineEmits<{ 'update:start': [value: string]; 'update:end': [value: string]; apply: [] }>()
const startModel = computed({ get: () => props.start, set: (value) => emit('update:start', value) })
const endModel = computed({ get: () => props.end, set: (value) => emit('update:end', value) })
const preset = ref('custom')
const iso = (value: Date) => {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
async function applyPreset() {
  const today = new Date()
  let from = new Date(today)
  let to = new Date(today)
  if (preset.value === 'week') from.setDate(today.getDate() - ((today.getDay() + 6) % 7))
  else if (preset.value === 'last-week') { to.setDate(today.getDate() - ((today.getDay() + 6) % 7) - 1); from = new Date(to); from.setDate(to.getDate() - 6) }
  else if (preset.value === 'month') from = new Date(today.getFullYear(), today.getMonth(), 1)
  else if (preset.value === '30') from.setDate(today.getDate() - 29)
  else if (preset.value === '90') from.setDate(today.getDate() - 89)
  else if (preset.value === '180') from.setDate(today.getDate() - 179)
  else if (preset.value === 'year') from = new Date(today.getFullYear(), 0, 1)
  else return
  emit('update:start', iso(from))
  emit('update:end', iso(to))
  await nextTick()
  emit('apply')
}
</script>

<template>
  <div class="filters" aria-label="Datumsfilter">
    <label class="field">Zeitraum<select v-model="preset" @change="applyPreset"><option value="custom">Benutzerdefiniert</option><option value="week">Aktuelle Woche</option><option value="last-week">Letzte Woche</option><option value="month">Aktueller Monat</option><option value="30">Letzte 30 Tage</option><option value="90">Letzte 90 Tage</option><option value="180">Letzte 180 Tage</option><option value="year">Aktuelles Jahr</option></select></label>
    <label class="field">Von <input v-model="startModel" type="date" /></label>
    <label class="field">Bis <input v-model="endModel" type="date" /></label>
    <button class="button secondary" type="button" @click="emit('apply')">Anwenden</button>
  </div>
</template>
