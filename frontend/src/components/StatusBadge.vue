<script setup lang="ts">
import { i18n } from '../i18n'
import type { TrackingStatus } from '../types'

const props = defineProps<{ status: TrackingStatus | string }>()
const t = i18n.global.t.bind(i18n.global)
const labels: Record<string, string> = {
  complete: 'status.complete',
  probably_complete: 'status.complete',
  probably_incomplete: 'status.incomplete',
  incomplete: 'status.incomplete',
  no_data: 'status.noData',
  completed: 'status.completed',
  completed_with_errors: 'status.withErrors',
  partial_failed: 'status.partial',
}
</script>

<template>
  <span :class="['badge', props.status.replaceAll('_', '-')]">
    <span aria-hidden="true">●</span>{{ labels[props.status] ? t(labels[props.status]) : props.status }}
  </span>
</template>
