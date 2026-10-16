<script setup lang="ts">
import { graphic } from 'echarts'
import type { EChartsOption, LineSeriesOption } from 'echarts'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { AnalysisIcon, analysisHubQuery, analysisRangeDayCount, localizedAnalysisPresentation } from '../analysis-v2'
import { analysisRangeWithinDays, periodFromQuery, resolveAllDataRange } from '../analytics-period'
import type { AppliedPeriod, PeriodLimits, PeriodRange } from '../analytics-period'
import {
  DEFAULT_NUTRITION_SECTION_ORDER, projectNutritionMeals, moveNutritionSection,
  normalizeNutritionSectionOrder, nutritionSectionOrderStorageKey,
} from '../analysis-nutrition-overview'
import type { NutritionSectionId } from '../analysis-nutrition-overview'
import { chartColors } from '../chart-theme'
import ChartPanel from '../components/ChartPanel.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import NutritionSectionOrderControl from '../components/NutritionSectionOrderControl.vue'
import { formatDate, formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisNutritionResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const pagePath = route.path
const router = useRouter()
const auth = useAuthStore()
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const kcal = createNumberFormatter({ maximumFractionDigits: 0 })
const maxDays = 365
const limits = computed<PeriodLimits>(() => ({ minDays: 1, maxDays, customRange: true, maxDate: today.value }))
const response = ref<AnalysisNutritionResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0
let requestedRange = ''
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'
let syncingQuery: ReturnType<typeof analysisHubQuery> | null = null

const selection = computed(() => periodFromQuery(
  route.query, limits.value, today.value,
  route.query.period === 'all' ? committedAllRange.value : allRange.value,
))
const start = computed(() => selection.value.start)
const end = computed(() => selection.value.end)
const period = computed(() => selection.value.period)
const chartDates = computed(() => response.value ? Array.from(
  { length: analysisRangeDayCount({ start: start.value, end: end.value }) },
  (_, index) => shiftIsoDate(start.value, index),
) : [])
const definitions = [
  { key: 'dietary_energy_kcal', color: 'violet', unit: 'kcal', symbol: 'circle' },
  { key: 'protein_g', color: 'teal', unit: 'g', symbol: 'circle' },
  { key: 'carbohydrates_g', color: 'blue', unit: 'g', symbol: 'diamond' },
  { key: 'fat_g', color: 'amber', unit: 'g', symbol: 'triangle' },
] as const
const metrics = computed(() => {
  const byDate = new Map(response.value?.days.map((day) => [day.date, day]) ?? [])
  return definitions.map((definition) => {
    const values = chartDates.value.map((date) => {
      const value = byDate.get(date)?.[definition.key]
      return value != null && Number.isFinite(value) ? value : null
    })
    let total = 0
    let count = 0
    for (const value of values) {
      if (value === null) continue
      total += value
      count += 1
    }
    return {
      ...definition,
      label: localizedAnalysisPresentation(definition.key)?.label ?? definition.key,
      icon: localizedAnalysisPresentation(definition.key)?.icon ?? 'nutrition',
      values,
      count,
      average: count ? `${(definition.unit === 'kcal' ? kcal : number).format(total / count)} ${definition.unit}` : '–',
    }
  })
})
const hasNutritionData = computed(() => metrics.value.some((metric) => metric.count > 0))
const hasEnergyData = computed(() => metrics.value[0]!.count > 0)
const hasMacroData = computed(() => metrics.value.slice(1).some((metric) => metric.count > 0))
const mealsExpanded = ref(false)
const mealProjection = computed(() => projectNutritionMeals(response.value?.days ?? []))
const mealGroups = computed(() => mealsExpanded.value ? mealProjection.value.all : mealProjection.value.preview)

watch([period, start, end], () => { mealsExpanded.value = false }, { flush: 'sync' })
const sectionOrder = ref<NutritionSectionId[]>([...DEFAULT_NUTRITION_SECTION_ORDER])
const orderAnnouncement = ref('')
const sectionLabels = computed(() => ({
  energy: metrics.value[0]!.label,
  macros: t('trends.macroChart'),
  meals: t('analysisV2.nutritionMeals'),
}))

watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], previousIdentity) => {
    const previousUserId = previousIdentity?.[0]
    const previousTimezone = previousIdentity?.[1]
    const accountChanged = userId !== previousUserId
    if (accountChanged) {
      orderAnnouncement.value = ''
      let stored: unknown = null
      if (userId && typeof window !== 'undefined') {
        try {
          const value = window.localStorage.getItem(nutritionSectionOrderStorageKey(userId))
          stored = value ? JSON.parse(value) : null
        } catch {
          // A disabled or malformed browser store must not block the overview.
        }
      }
      sectionOrder.value = normalizeNutritionSectionOrder(stored)
    }
    if (previousUserId === undefined || (userId === previousUserId && timezone === previousTimezone)) return

    latestLoadId += 1
    response.value = null
    error.value = ''
    requestedRange = ''
    if (accountChanged) committedAllRange.value = null
    requestedInitialAll = route.query.period === 'all'
    if (userId) {
      loading.value = true
      void load()
    } else {
      loading.value = false
    }
  },
  { immediate: true, flush: 'sync' },
)

function saveSectionOrder(order: NutritionSectionId[]): void {
  sectionOrder.value = order
  orderAnnouncement.value = t('analysisV2.nutritionOrderChanged', {
    sections: order.map((id) => sectionLabels.value[id]).join(', '),
  })
  const userId = auth.user?.id
  if (!userId || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(nutritionSectionOrderStorageKey(userId), JSON.stringify(order))
  } catch {
    // Keep reordering usable when browser storage is unavailable.
  }
}


function formatMealType(value: string | null): string {
  switch (value) {
    case 'breakfast':
      return t('nutritionVerification.mealTypes.breakfast')
    case 'lunch':
      return t('nutritionVerification.mealTypes.lunch')
    case 'dinner':
      return t('nutritionVerification.mealTypes.dinner')
    case 'snack':
      return t('nutritionVerification.mealTypes.snack')
    default:
      return t('nutritionVerification.noMealType')
  }
}

function withAlpha(color: string, alpha: number): string {
  const match = /^#([0-9a-f]{6})$/i.exec(color)
  if (!match) return color
  const value = Number.parseInt(match[1]!, 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`
}

const chartOptions = computed(() => {
  const palette = chartColors.value
  const series = metrics.value.map((metric, index): LineSeriesOption => {
    const color = palette[metric.color]
    const alphas = index === 0 ? [0.34, 0.18, 0.08, 0.02] : [0.045, 0.02, 0.008, 0]
    return {
      name: metric.label,
      type: 'line',
      smooth: false,
      connectNulls: false,
      showSymbol: true,
      symbol: metric.symbol,
      symbolSize: index === 0 ? 6 : 5,
      data: metric.values,
      lineStyle: { color, width: index === 0 ? 2.5 : 2 },
      itemStyle: { color },
      areaStyle: {
        // Both axes start at zero; the fade spans the full area down to the plot floor.
        origin: 'start',
        color: new graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(color, alphas[0]!) },
          { offset: 0.45, color: withAlpha(color, alphas[1]!) },
          { offset: 0.75, color: withAlpha(color, alphas[2]!) },
          { offset: 1, color: withAlpha(color, alphas[3]!) },
        ], false),
      },
    }
  })
  const option = (unit: string, lines: LineSeriesOption[]): EChartsOption => ({
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
    },
    grid: { top: 30, left: 12, right: 18, bottom: 12, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: chartDates.value,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, hideOverlap: true, formatter: (value: string | number) => formatGermanDate(String(value)) },
    },
    yAxis: {
      type: 'value',
      min: 0,
      name: unit,
      nameTextStyle: { color: palette.label },
      axisLabel: { color: palette.label },
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: lines,
  })
  return {
    energy: option('kcal', series.slice(0, 1)),
    macros: option('g', series.slice(1)),
  }
})

async function syncQuery(): Promise<void> {
  if (route.path !== pagePath) return
  const query = analysisHubQuery(period.value, selection.value)
  if (route.query.period === query.period
    && route.query.start === query.start && route.query.end === query.end) return
  syncingQuery = query
  await router.replace({
    query: { ...route.query, start: query.start, end: query.end, period: query.period },
    hash: route.hash,
  })
}

async function load(): Promise<void> {
  if (route.path !== pagePath) return
  const requestId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today.value) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.nutritionRangeInvalid')
    loading.value = false
    return
  }
  const range = { start: start.value, end: end.value }
  requestedRange = `${period.value}/${range.start}/${range.end}`
  response.value = null
  error.value = ''
  loading.value = true
  await syncQuery()
  if (requestId !== latestLoadId) return
  const params = new URLSearchParams(range)
  try {
    const result = await api<AnalysisNutritionResponse>(`/analytics/nutrition/analysis?${params.toString()}`)
    if (requestId !== latestLoadId) return
    response.value = result
    const admitted = allRange.value
    if (requestedInitialAll) {
      requestedInitialAll = false
      if (admitted) {
        committedAllRange.value = admitted
        await router.replace({
          path: route.path,
          query: { ...route.query, ...analysisHubQuery('all', admitted), start: undefined, end: undefined },
          hash: route.hash,
        })
        return
      }
    }
    if (period.value === 'all' && !admitted) {
      committedAllRange.value = null
      await load()
    }
  } catch (cause) {
    if (requestId === latestLoadId) {
      requestedInitialAll = false
      error.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (requestId === latestLoadId) loading.value = false
  }
}

function applyPeriod(value: AppliedPeriod): void {
  requestedInitialAll = false
  const selected = periodFromQuery(value, limits.value, today.value, allRange.value)
  committedAllRange.value = selected.period === 'all'
    ? { start: selected.start, end: selected.end } : null
  void router.push({
    path: route.path,
    query: {
      ...route.query,
      ...analysisHubQuery(selected.period, selected),
      start: selected.period === 'custom' ? selected.start : undefined,
      end: selected.period === 'custom' ? selected.end : undefined,
    },
    hash: route.hash,
  })
}

watch(() => route.path, (path) => {
  if (path !== pagePath) {
    latestLoadId += 1
    requestedInitialAll = false
  }
}, { flush: 'sync' })
watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod], [previousStart, previousEnd, previousPeriod]) => {
    if (route.path !== pagePath) return
    const ownNormalization = syncingQuery
      && nextPeriod === syncingQuery.period && nextStart === syncingQuery.start && nextEnd === syncingQuery.end
    syncingQuery = null
    if (ownNormalization) return
    if (nextStart !== previousStart || nextEnd !== previousEnd || nextPeriod !== previousPeriod) requestedInitialAll = false
    const selected = periodFromQuery(route.query, limits.value, today.value, committedAllRange.value ?? allRange.value)
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    if (requestedRange === `${period.value}/${start.value}/${end.value}`) void syncQuery()
  },
  { flush: 'sync' },
)
watch(
  () => [start.value, end.value, period.value] as const,
  () => {
    if (requestedRange !== `${period.value}/${start.value}/${end.value}`) void load()
    else void syncQuery()
  },
  { immediate: true },
)
onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="analysis-nutrition-overview">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: { ...route.query, ...analysisHubQuery(period, { start, end }) }, hash: route.hash }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ t('analysisV2.categories.nutrition') }}</span>
    </nav>
    <div class="page-heading page-heading--period">
      <h1><AnalysisIcon icon="nutrition" />{{ t('analysisV2.categories.nutrition') }}</h1>
      <PeriodPicker
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :max-date="today"
        :period-capabilities="capability"
        :all-range="allRange"
        @apply="applyPeriod"
      />
    </div>
    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response">
      <p v-if="!hasNutritionData && !mealGroups.length" class="card empty nutrition-empty" role="status">{{ t('analysisV2.nutritionEmpty') }}</p>
      <section v-else class="card nutrition-overview-kpis" :aria-label="t('analysisV2.nutritionMetrics')">
        <RouterLink
          v-for="metric in metrics"
          :key="metric.key"
          class="insight-card nutrition-overview-kpi"
          :to="{ path: `/analyse/ernaehrung/${metric.key}`, query: route.query, hash: route.hash }"
          :style="{ '--metric-color': chartColors[metric.color] }"
        >
          <span class="nutrition-overview-kpi-heading">
            <span class="nutrition-overview-kpi-label"><AnalysisIcon :icon="metric.icon" />{{ metric.label }}</span>
            <span class="nutrition-overview-kpi-caption" role="img" :title="t('analysisV2.averagePerDay')" :aria-label="t('analysisV2.averagePerDay')">⌀</span>
          </span>
          <strong class="nutrition-overview-kpi-value">{{ metric.average }}</strong>
        </RouterLink>
      </section>
      <span class="nutrition-order-announcement" role="status" aria-live="polite">{{ orderAnnouncement }}</span>
      <div
        v-for="(section, sectionIndex) in sectionOrder"
        :key="section"
        class="nutrition-section"
        :data-nutrition-section="section"
      >
        <ChartPanel
          v-if="section === 'energy'"
          :title="metrics[0]!.label"
          :option="chartOptions.energy"
          :empty="!hasEnergyData"
        >
          <template #title>
            <RouterLink class="nutrition-chart-title-link" :to="{ path: '/analyse/ernaehrung/dietary_energy_kcal', query: route.query, hash: route.hash }">{{ metrics[0]!.label }}</RouterLink>
          </template>
          <template #header-actions>
            <NutritionSectionOrderControl
              :label="sectionLabels[section]"
              :can-move-up="sectionIndex > 0"
              :can-move-down="sectionIndex < sectionOrder.length - 1"
              @move="saveSectionOrder(moveNutritionSection(sectionOrder, section, $event))"
            />
          </template>
        </ChartPanel>
        <ChartPanel
          v-else-if="section === 'macros'"
          :title="t('trends.macroChart')"
          :option="chartOptions.macros"
          :empty="!hasMacroData"
        >
          <template #header-actions>
            <div class="nutrition-macro-actions">
              <div class="nutrition-macro-legend">
                <RouterLink
                  v-for="metric in metrics.slice(1)"
                  :key="metric.key"
                  :to="{ path: `/analyse/ernaehrung/${metric.key}`, query: route.query, hash: route.hash }"
                  :style="{ '--metric-color': chartColors[metric.color] }"
                >
                  <span class="nutrition-macro-swatch" aria-hidden="true"></span>{{ metric.label }}
                </RouterLink>
              </div>
              <NutritionSectionOrderControl
                :label="sectionLabels[section]"
                :can-move-up="sectionIndex > 0"
                :can-move-down="sectionIndex < sectionOrder.length - 1"
                @move="saveSectionOrder(moveNutritionSection(sectionOrder, section, $event))"
              />
            </div>
          </template>
        </ChartPanel>
        <section v-else class="card table-card nutrition-meals" aria-labelledby="nutrition-meals-title">
          <div class="chart-card-header">
            <h2 id="nutrition-meals-title">{{ t('analysisV2.nutritionMeals') }}</h2>
            <NutritionSectionOrderControl
              :label="sectionLabels[section]"
              :can-move-up="sectionIndex > 0"
              :can-move-down="sectionIndex < sectionOrder.length - 1"
              @move="saveSectionOrder(moveNutritionSection(sectionOrder, section, $event))"
            />
          </div>
          <div id="nutrition-meals-content">
            <div v-for="group in mealGroups" :key="group.date" class="nutrition-meal-date-group">
              <h3 class="nutrition-meal-date"><time :datetime="group.date">{{ formatDate(group.date) }}</time></h3>
              <div
                v-for="occasion in group.occasions"
                :key="occasion.key"
                class="nutrition-meal-occasion"
                :data-meal-type="occasion.type ?? ''"
              >
                <h4 class="nutrition-meal-occasion-title">
                  {{ formatMealType(occasion.type) }}<span v-if="occasion.time"> · {{ occasion.time.slice(0, 5) }}</span>
                </h4>
                <ul class="nutrition-meal-entry-list">
                  <li v-for="meal in occasion.entries" :key="meal.eventId">
                    <RouterLink
                      class="nutrition-meal-link"
                      data-meal-entry
                      :to="{ path: `/ernaehrung/eintrag/${meal.eventId}`, query: { ...route.query, from: 'analysis' } }"
                    >
                      <span class="nutrition-meal-name">{{ meal.name }}</span>
                      <span v-if="meal.energy != null && Number.isFinite(meal.energy)" class="nutrition-meal-energy">{{ kcal.format(meal.energy) }} kcal</span>
                    </RouterLink>
                  </li>
                </ul>
                <p v-if="occasion.entryCount > 1 && occasion.totalEnergy !== null" class="nutrition-meal-subtotal">
                  <span>{{ t('analysisV2.nutritionOccasionTotal', { type: formatMealType(occasion.type) }) }}</span>
                  <strong>{{ kcal.format(occasion.totalEnergy) }} kcal</strong>
                </p>
              </div>
            </div>
          </div>
          <button
            v-if="mealProjection.hasMore"
            class="nutrition-meals-disclosure"
            type="button"
            :aria-expanded="mealsExpanded"
            aria-controls="nutrition-meals-content"
            @click="mealsExpanded = !mealsExpanded"
          >{{ t(mealsExpanded ? 'analysisV2.nutritionShowFewerMeals' : 'analysisV2.nutritionShowAllMeals') }}</button>
          <div v-if="!mealGroups.length" class="nutrition-meals-empty">
            <p>{{ t('analysisV2.nutritionNoNamedMeals') }}</p>
            <RouterLink :to="{ path: '/ernaehrung', query: { date: response.end_date } }">{{ t('analysisV2.nutritionOpenDaily') }}</RouterLink>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.analysis-nutrition-overview { display: grid; gap: 1rem; min-width: 0; }
.analysis-nutrition-overview > .page-heading { margin-bottom: 0; }
.nutrition-section { min-width: 0; }
.nutrition-section :deep(.chart-card) { min-height: 0; }
.nutrition-section :deep(.chart-card-header) { flex-wrap: wrap; gap: .5rem; }
.nutrition-order-announcement { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.page-heading h1 { display: flex; align-items: center; gap: .65rem; margin: 0; }
.page-heading h1 svg { flex-shrink: 0; color: var(--accent); }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.nutrition-overview-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); padding: 0; }
.nutrition-overview-kpi { grid-template-columns: minmax(0, 1fr); align-content: center; gap: .35rem; min-width: 0; color: var(--ink); text-decoration: none; border-radius: 0; }
.nutrition-overview-kpi:first-child { border-top-left-radius: inherit; border-bottom-left-radius: inherit; }
.nutrition-overview-kpi:last-child { border-top-right-radius: inherit; border-bottom-right-radius: inherit; }
.nutrition-overview-kpi-heading { display: flex; align-items: center; gap: .5rem; min-width: 0; }
.nutrition-overview-kpi + .nutrition-overview-kpi { border-left: 1px solid var(--border); }
.nutrition-overview-kpi:hover { background: color-mix(in srgb, var(--metric-color) 6%, var(--surface)); }
.nutrition-overview-kpi-label { display: flex; flex: 0 1 auto; min-width: 0; align-items: center; gap: .45rem; font-weight: 600; font-size: .92rem; overflow-wrap: normal; }
.nutrition-overview-kpi-label svg { flex-shrink: 0; color: var(--metric-color); }
.nutrition-overview-kpi .nutrition-overview-kpi-value { margin: 0; font-size: 1.2rem; line-height: 1.25; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; white-space: normal; }
.nutrition-overview-kpi-caption { flex: 0 0 auto; min-width: 0; color: var(--muted); font-size: .75rem; line-height: 1; white-space: nowrap; }
.nutrition-overview-kpi:focus-visible, .analysis-breadcrumbs a:focus-visible, .error button:focus-visible, .nutrition-chart-title-link:focus-visible, .nutrition-macro-legend a:focus-visible, .nutrition-meals a:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.nutrition-empty { margin: 0; padding: 1rem; }
.nutrition-chart-title-link { color: inherit; text-decoration: none; }
.nutrition-chart-title-link:hover { text-decoration: underline; }
.nutrition-macro-legend { display: flex; flex-wrap: wrap; gap: .4rem .8rem; }
.nutrition-macro-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .5rem 1rem; }
.nutrition-macro-legend a { display: inline-flex; align-items: center; gap: .35rem; padding: .25rem 0; color: var(--ink); font-size: .85rem; text-decoration: none; }
.nutrition-macro-legend a:hover { text-decoration: underline; }
.nutrition-macro-swatch { width: .6rem; height: .6rem; flex-shrink: 0; border-radius: 50%; background: var(--metric-color); }
.nutrition-meals { min-width: 0; margin-top: 0; padding: 1rem 1.05rem .9rem; }
.nutrition-meal-date-group + .nutrition-meal-date-group { margin-top: 1.1rem; border-top: 1px solid var(--border-strong); padding-top: .55rem; }
.nutrition-meal-date { margin: .35rem .25rem .5rem; color: var(--muted-strong); font-size: .8rem; font-weight: 600; }
.nutrition-meal-occasion + .nutrition-meal-occasion { margin-top: .4rem; }
.nutrition-meal-occasion-title { margin: .3rem .25rem .1rem; color: var(--muted-strong); font-size: .86rem; font-weight: 600; line-height: 1.25; }
.nutrition-meal-entry-list { margin: 0; padding: 0; list-style: none; }
.nutrition-meal-entry-list li + li { border-top: 1px solid var(--border); }
.nutrition-meal-link { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: .35rem .8rem; min-width: 0; padding: .5rem .25rem; color: var(--ink); text-decoration: none; border-radius: .35rem; }
.nutrition-meal-link:hover { background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
.nutrition-meal-name { min-width: 0; font-size: .92rem; overflow-wrap: anywhere; }
.nutrition-meal-energy { font-size: .9rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.nutrition-meal-subtotal { display: flex; justify-content: flex-end; align-items: baseline; gap: .5rem; margin: 0 .25rem .3rem; padding: .35rem 0; border-top: 1px solid var(--border); color: var(--muted-strong); font-size: .88rem; }
.nutrition-meal-subtotal strong { color: var(--ink); font-variant-numeric: tabular-nums; white-space: nowrap; }
.nutrition-meals-disclosure { margin: .6rem 0 0; padding: .35rem .25rem; border: 0; background: transparent; color: var(--muted-strong); font: inherit; font-size: .88rem; text-align: left; cursor: pointer; }
.nutrition-meals-disclosure:hover { color: var(--ink); text-decoration: underline; }
.nutrition-meals-disclosure:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.nutrition-meals-empty { display: flex; flex-wrap: wrap; align-items: baseline; gap: .35rem .8rem; color: var(--muted-strong); font-size: .9rem; }
.nutrition-meals-empty p { margin: 0; }
@media (max-width: 900px) {
  .nutrition-overview-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .nutrition-overview-kpi:first-child { border-bottom-left-radius: 0; }
  .nutrition-overview-kpi:nth-child(2) { border-top-right-radius: inherit; }
  .nutrition-overview-kpi:nth-child(3) { border-left: 0; border-bottom-left-radius: inherit; }
  .nutrition-overview-kpi:last-child { border-top-right-radius: 0; }
  .nutrition-overview-kpi:nth-child(n + 3) { border-top: 1px solid var(--border); }
}
@media (max-width: 600px) {
  .analysis-nutrition-overview > .page-heading { min-width: 0; align-items: stretch; }
  .page-heading > .analysis-period-picker { width: 100%; }
  .nutrition-overview-kpi-label { font-size: .8rem; }
}
</style>
