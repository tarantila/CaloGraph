<script setup lang="ts">
import { PhCheckCircle, PhMinusCircle, PhXCircle } from '@phosphor-icons/vue'
import { computed } from 'vue'

import { i18n } from '../i18n'

const props = withDefaults(defineProps<{
  outcome: string
  showLabel?: boolean
}>(), {
  showLabel: false,
})

const t = i18n.global.t.bind(i18n.global)

const label = computed(() => {
  const key = `adminOutcomes.${props.outcome}`
  return i18n.global.te(key) ? t(key) : props.outcome
})
const statusClass = computed(() => {
  if (props.outcome === 'success') return 'success'
  if (props.outcome === 'failure') return 'failure'
  return 'neutral'
})
const icon = computed(() => {
  if (props.outcome === 'success') return PhCheckCircle
  if (props.outcome === 'failure') return PhXCircle
  return PhMinusCircle
})
</script>

<template>
  <span
    :class="['audit-outcome', { 'with-label': showLabel }, statusClass]"
    :aria-label="label"
    :title="showLabel ? undefined : label"
  >
    <component :is="icon" :size="18" weight="fill" aria-hidden="true" />
    <span v-if="showLabel">{{ label }}</span>
  </span>
</template>
