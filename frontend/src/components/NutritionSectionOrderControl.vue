<script setup lang="ts">
import { i18n } from '../i18n'

defineProps<{ label: string; canMoveUp: boolean; canMoveDown: boolean }>()
const emit = defineEmits<{
  move: [direction: -1 | 1]
}>()
const t = i18n.global.t.bind(i18n.global)
</script>

<template>
  <div class="nutrition-section-order-controls">
    <button
      type="button"
      data-move-up
      :disabled="!canMoveUp"
      :aria-label="t('analysisV2.nutritionMoveUp', { label })"
      @click="emit('move', -1)"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m4 10 4-4 4 4" /></svg>
    </button>
    <button
      type="button"
      data-move-down
      :disabled="!canMoveDown"
      :aria-label="t('analysisV2.nutritionMoveDown', { label })"
      @click="emit('move', 1)"
    >
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5"><path d="m4 6 4 4 4-4" /></svg>
    </button>
  </div>
</template>

<style scoped>
.nutrition-section-order-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .15rem; }
button { display: grid; place-items: center; width: 44px; height: 44px; padding: 0; border: 1px solid transparent; border-radius: .4rem; background: transparent; color: var(--muted-strong); }
button:hover:not(:disabled) { background: var(--surface-soft); border-color: var(--border); color: var(--ink); }
button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
button:disabled { opacity: .35; cursor: default; }
</style>
