<script setup lang="ts">
import type { TrackingStatus } from '../types'

const props = defineProps<{ status: TrackingStatus | string }>()
const labels: Record<string, string> = {
  complete: 'Vollständig',
  probably_complete: 'Wahrscheinlich vollständig',
  probably_incomplete: 'Wahrscheinlich unvollständig',
  incomplete: 'Unvollständig',
  no_data: 'Keine Daten',
  completed: 'Abgeschlossen',
  completed_with_errors: 'Mit Fehlern',
}
</script>

<template>
  <span :class="['badge', props.status.replaceAll('_', '-')]">
    <span aria-hidden="true">●</span>{{ labels[props.status] ?? props.status }}
  </span>
</template>

