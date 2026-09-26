<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
const router = useRouter()
const route = useRoute()
const analysisContent = ref<HTMLElement | null>(null)

const analysisAreas = [
  { name: 'analysis-overview', label: 'overview' },
  { name: 'analysis-nutrition', label: 'nutrition' },
  { name: 'analysis-micronutrients', label: 'micronutrients' },
  { name: 'analysis-activity', label: 'activity' },
  { name: 'analysis-body', label: 'body' },
] as const
const analysisRouteNames = new Set<string>(analysisAreas.map((area) => area.name))
const selectedArea = computed(() => (
  route.name === 'analysis-micronutrient-detail' ? 'analysis-micronutrients' : route.name
))
let layoutMounted = false

function isActive(name: string): boolean {
  return selectedArea.value === name
}

function selectArea(event: Event): void {
  const name = (event.target as HTMLSelectElement).value
  if (analysisRouteNames.has(name)) void router.push({ name })
}

async function focusAnalysisHeading(): Promise<void> {
  await nextTick()
  const heading = analysisContent.value?.querySelector<HTMLElement>('h1')
  if (!heading) return
  if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1
  heading.classList.add('analysis-heading-focus')
  heading.focus()
}

onMounted(() => {
  layoutMounted = true
  void focusAnalysisHeading()
})

watch(
  () => route.path,
  async (_, previousPath) => {
    if (!layoutMounted || previousPath === undefined || !analysisRouteNames.has(selectedArea.value as string)) return
    await focusAnalysisHeading()
  },
  { flush: 'post' },
)
</script>

<template>
  <div class="analysis-layout">
    <nav class="analysis-navigation" :aria-label="t('analysisNav.aria')">
      <RouterLink
        v-for="area in analysisAreas"
        :key="area.name"
        :to="{ name: area.name }"
        :class="{ active: isActive(area.name) }"
        :aria-current="isActive(area.name) ? 'page' : undefined"
      >
        {{ t(`analysisNav.${area.label}`) }}
      </RouterLink>
    </nav>
    <nav class="analysis-mobile-navigation" :aria-label="t('analysisNav.aria')">
      <label for="analysis-area">
        <span>{{ t('analysisNav.choose') }}</span>
        <select id="analysis-area" name="analysis-area" :value="selectedArea" @change="selectArea">
          <option v-for="area in analysisAreas" :key="area.name" :value="area.name">
            {{ t(`analysisNav.${area.label}`) }}
          </option>
        </select>
      </label>
    </nav>
    <div ref="analysisContent" class="analysis-content">
      <RouterView />
    </div>
  </div>
</template>
