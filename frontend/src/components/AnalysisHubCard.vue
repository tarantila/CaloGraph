<script setup lang="ts">
import { PhArrowDown, PhArrowUp, PhTrash } from '@phosphor-icons/vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

import { AnalysisIcon } from '../analysis-v2'
import { i18n } from '../i18n'

type SortGroup = 'summary' | 'metric'
type CardState = 'loading' | 'empty' | 'ready' | 'error'

const props = defineProps<{
  group: SortGroup
  cardKey: string
  label: string
  icon: string
  to?: RouteLocationRaw
  state: CardState
  error: string
  emptyMessage?: string
  saving: boolean
  menuOpen: boolean
  canMoveBefore: boolean
  canMoveAfter: boolean
  removable: boolean
  dragging: boolean
  dailyAverage?: boolean
}>()

const emit = defineEmits<{
  dragStart: [event: PointerEvent]
  toggleMenu: []
  move: [offset: -1 | 1]
  remove: []
  retry: []
}>()

const t = i18n.global.t.bind(i18n.global)
</script>

<template>
  <li
    class="card analysis-card"
    :class="{ 'is-dragging': props.dragging }"
    :data-card-key="props.cardKey"
    :data-analysis-summary="props.group === 'summary' ? props.cardKey : undefined"
    :data-analysis-key="props.group === 'metric' ? props.cardKey : undefined"
  >
    <header class="analysis-card-header">
      <span class="analysis-card-icon" :data-icon="props.icon" aria-hidden="true"><AnalysisIcon :icon="props.icon" /></span>
      <h3 class="analysis-card-heading">
        <RouterLink v-if="props.to" :to="props.to">{{ props.label }}</RouterLink>
        <span v-else>{{ props.label }}</span>
        <abbr
          v-if="props.dailyAverage"
          class="analysis-average-mark"
          :title="t('analysisV2.averagePerDay')"
          :aria-label="t('analysisV2.averagePerDay')"
        >⌀</abbr>
      </h3>
      <button
        class="analysis-drag-handle"
        type="button"
        :disabled="props.saving || (!props.canMoveBefore && !props.canMoveAfter)"
        :aria-label="t('analysisV2.dragHandle', { label: props.label })"
        @pointerdown.prevent="emit('dragStart', $event)"
        @click.prevent.stop
      ><span class="analysis-drag-grip" aria-hidden="true"></span></button>
      <div class="analysis-overflow">
        <button
          class="analysis-overflow-trigger"
          type="button"
          :aria-expanded="props.menuOpen"
          :aria-label="t('analysisV2.cardActions', { label: props.label })"
          @click="emit('toggleMenu')"
        ><span class="analysis-overflow-dots" aria-hidden="true"></span></button>
        <div v-if="props.menuOpen" class="analysis-overflow-menu">
          <button
            type="button"
            :disabled="props.saving || !props.canMoveBefore"
            :aria-label="t('analysisV2.moveForward', { label: props.label })"
            @click="emit('move', -1)"
          >
            <PhArrowUp size="16" weight="regular" aria-hidden="true" />
            <span>{{ t('analysisV2.moveForwardShort') }}</span>
          </button>
          <button
            type="button"
            :disabled="props.saving || !props.canMoveAfter"
            :aria-label="t('analysisV2.moveBackward', { label: props.label })"
            @click="emit('move', 1)"
          >
            <PhArrowDown size="16" weight="regular" aria-hidden="true" />
            <span>{{ t('analysisV2.moveBackwardShort') }}</span>
          </button>
          <button
            v-if="props.removable"
            data-card-remove
            type="button"
            :disabled="props.saving"
            :aria-label="t('analysisV2.removeCard', { label: props.label })"
            @click="emit('remove')"
          >
            <PhTrash size="16" weight="regular" aria-hidden="true" />
            <span>{{ t('analysisV2.remove') }}</span>
          </button>
        </div>
      </div>
    </header>

    <p v-if="props.state === 'loading'" class="analysis-card-state" role="status">{{ t('common.loading') }}</p>
    <div v-else-if="props.state === 'error'" class="analysis-card-error" role="alert">
      <p>{{ props.error }}</p>
      <button class="button secondary" type="button" @click="emit('retry')">{{ t('analysisV2.retry') }}</button>
    </div>
    <p v-else-if="props.state === 'empty'" class="analysis-card-state">{{ props.emptyMessage ?? t('analysisV2.hubNoDataPeriod') }}</p>
    <div v-else class="analysis-card-content"><slot /></div>

    <footer v-if="props.to || props.state === 'ready'" class="analysis-card-footer">
      <div class="analysis-card-footer-copy"><slot name="footer" /></div>
      <span v-if="props.to" class="analysis-card-caret" aria-hidden="true"></span>
    </footer>
  </li>
</template>

<style scoped>
.analysis-card { position: relative; display: grid; grid-template-rows: auto auto auto; align-content: start; gap: .5rem; min-width: 0; padding: .75rem; transition: box-shadow .16s ease, border-color .16s ease; }
.analysis-card-reorder-move { transition: transform .25s cubic-bezier(.2, .7, .2, 1); }
.analysis-card-header { display: grid; grid-template-columns: 2rem minmax(0, 1fr) auto auto; align-items: center; column-gap: .35rem; row-gap: .25rem; min-width: 0; }
.analysis-card-icon { display: grid; place-items: center; grid-row: 1; width: 2rem; height: 2rem; border-radius: .55rem; color: var(--analysis-icon-color, var(--chart-series-violet)); background: color-mix(in srgb, var(--analysis-icon-color, var(--chart-series-violet)) 10%, transparent); font-size: 1.1rem; }
 .analysis-card-heading { grid-column: 2; grid-row: 1; align-self: center; min-width: 0; margin: 0; overflow-wrap: anywhere; }
.analysis-card-heading a { color: inherit; text-decoration: none; }
.analysis-card-heading a:hover { color: var(--accent-hover); }
.analysis-card-heading a::after { content: ''; position: absolute; inset: .65rem; z-index: 1; }
.analysis-card-heading a:focus-visible, .analysis-drag-handle:focus-visible, .analysis-overflow-trigger:focus-visible, .analysis-overflow-menu button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; border-radius: .25rem; }
.analysis-drag-handle { position: relative; z-index: 3; display: grid; place-items: center; grid-column: 3; grid-row: 1; min-width: 44px; min-height: 44px; padding: 0; border: 0; border-radius: .35rem; background: transparent; color: var(--muted); cursor: grab; touch-action: none; user-select: none; }
.analysis-drag-grip { width: .8rem; height: 1rem; background: radial-gradient(circle, currentColor 1px, transparent 1.15px) 0 0 / .4rem .4rem; }
.analysis-drag-handle:hover, .analysis-drag-handle:focus-visible { background: color-mix(in srgb, var(--muted) 10%, var(--surface)); color: var(--ink); }
.analysis-drag-handle:active { cursor: grabbing; }
.analysis-drag-handle:disabled { cursor: not-allowed; }
.analysis-overflow { position: relative; grid-column: 4; grid-row: 1; z-index: 3; }
.analysis-overflow-trigger { display: grid; place-items: center; min-width: 44px; min-height: 44px; padding: 0; border: 0; border-radius: .35rem; background: transparent; color: var(--muted); cursor: pointer; }
.analysis-overflow-trigger:hover, .analysis-overflow-trigger:focus-visible { background: color-mix(in srgb, var(--muted) 10%, var(--surface)); color: var(--ink); }
.analysis-overflow-dots { width: .23rem; height: .23rem; border-radius: 50%; background: currentColor; box-shadow: -.35rem 0 currentColor, .35rem 0 currentColor; }
.analysis-overflow-menu { position: absolute; top: calc(100% + .25rem); right: 0; z-index: 4; display: grid; width: min(12rem, 75vw); box-sizing: border-box; padding: .3rem; border: 1px solid var(--border); border-radius: .55rem; background: var(--popover); box-shadow: var(--shadow-popover); }
.analysis-overflow-menu button { display: flex; align-items: center; gap: .5rem; width: 100%; min-height: 44px; padding: .35rem .5rem; border: 0; border-radius: .35rem; background: transparent; color: var(--ink); font: inherit; font-size: .82rem; text-align: left; cursor: pointer; }
.analysis-overflow-menu button svg { flex: 0 0 1rem; width: 1rem; height: 1rem; }
.analysis-overflow-menu button:hover, .analysis-overflow-menu button:focus-visible { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-overflow-menu button:disabled { color: var(--muted); cursor: not-allowed; }
.analysis-card-content { display: grid; align-content: start; gap: .4rem; min-width: 0; min-height: 0; padding: .1rem .15rem; color: var(--ink); font-weight: 400; }
.analysis-card-state { align-self: center; margin: 0; color: var(--muted); overflow-wrap: anywhere; }
.analysis-card-error { display: grid; justify-items: start; align-content: start; gap: .5rem; min-width: 0; color: var(--danger); overflow-wrap: anywhere; }
.analysis-card-error p { margin: 0; }
.analysis-card-error button { position: relative; z-index: 2; }
.analysis-card-footer { display: flex; align-items: center; justify-content: space-between; gap: .5rem; min-width: 0; padding: .4rem .15rem 0; border-top: 1px solid var(--border); }
.analysis-card-footer-copy { min-width: 0; color: var(--muted); font-size: .75rem; line-height: 1.4; font-weight: 400; }
.analysis-card-caret { position: relative; z-index: 2; margin-left: auto; width: .5rem; height: .5rem; border-top: 2px solid var(--muted); border-right: 2px solid var(--muted); transform: rotate(45deg); pointer-events: none; }
.analysis-card.is-dragging { --analysis-card-drag-scale: 1.025; z-index: 4; translate: var(--drag-x, 0px) var(--drag-y, 0px); scale: var(--analysis-card-drag-scale); border-color: var(--accent); box-shadow: var(--shadow-popover); pointer-events: none; }
@media (min-width: 1400px) {
  .analysis-card-header { grid-template-columns: 1.7rem minmax(0, 1fr) auto auto; column-gap: .35rem; }
  .analysis-card-icon { width: 1.7rem; height: 1.7rem; }
}
@media (min-width: 1201px) {
  .analysis-card { height: 18rem; box-sizing: border-box; grid-template-rows: auto minmax(0, 1fr) auto; align-content: stretch; }
}
@media (prefers-reduced-motion: reduce) {
  .analysis-card-reorder-move, .analysis-card { transition: none; }
  .analysis-card.is-dragging { --analysis-card-drag-scale: 1; transform: none; }
}
</style>
