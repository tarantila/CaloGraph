<script setup lang="ts">
import type { TrackingStatus } from '../types'

const props = defineProps<{ status: TrackingStatus | string }>()
const labels: Record<string, string> = {
  complete: 'Erfasst',
  probably_complete: 'Erfasst',
  probably_incomplete: 'Kalorienwert fehlt',
  incomplete: 'Kalorienwert fehlt',
  no_data: 'Keine Daten',
  completed: 'Abgeschlossen',
  completed_with_errors: 'Mit Fehlern',
  partial_failed: 'Teilweise importiert',
}
</script>

<template>
  <span :class="['badge', props.status.replaceAll('_', '-')]">
    <span aria-hidden="true">●</span>{{ labels[props.status] ?? props.status }}
  </span>
</template>
