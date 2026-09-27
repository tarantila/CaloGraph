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
