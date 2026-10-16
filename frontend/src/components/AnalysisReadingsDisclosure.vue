<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  headingId: string
  title: string
}>()

const expanded = ref(false)
const panelId = computed(() => `${props.headingId}-panel`)
</script>

<template>
  <section>
    <h2 :id="props.headingId" class="analysis-readings-heading">
      <button
        class="analysis-readings-toggle"
        type="button"
        :aria-expanded="expanded"
        :aria-controls="panelId"
        @click="expanded = !expanded"
      >
        <span>{{ props.title }}</span>
        <span class="analysis-readings-chevron" :class="{ 'is-expanded': expanded }" aria-hidden="true"></span>
      </button>
    </h2>
    <div :id="panelId" v-show="expanded">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.analysis-readings-heading { margin-top: 0; font-size: 1.05rem; }
.analysis-readings-toggle { display: flex; align-items: center; justify-content: space-between; gap: .75rem; width: 100%; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.analysis-readings-toggle:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 3px; }
.analysis-readings-chevron { flex: 0 0 auto; width: .5rem; height: .5rem; margin-right: .2rem; border-top: 2px solid var(--muted); border-right: 2px solid var(--muted); transform: rotate(135deg); pointer-events: none; }
.analysis-readings-chevron.is-expanded { transform: rotate(-45deg); }
</style>
