<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const analysisContent = ref<HTMLElement | null>(null)

async function focusAnalysisHeading(): Promise<void> {
  await nextTick()
  const heading = analysisContent.value?.querySelector<HTMLElement>('h1')
  if (!heading) return
  if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1
  heading.classList.add('analysis-heading-focus')
  heading.focus()
}

onMounted(() => { void focusAnalysisHeading() })
watch(() => route.path, () => { void focusAnalysisHeading() }, { flush: 'post' })
</script>

<template>
  <div class="analysis-layout">
    <div ref="analysisContent" class="analysis-content">
      <RouterView />
    </div>
  </div>
</template>
<style scoped>
@media (min-width: 601px) and (max-width: 800px) {
  .analysis-layout :deep(.page-heading) {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (min-width: 801px) and (max-width: 900px) {
  .analysis-layout :deep(.page-heading > div:first-child) { min-width: 0; }
  .analysis-layout :deep(.page-heading > .analysis-period-picker) { min-width: max-content; }
}
</style>
