<script setup lang="ts">
import { PhBarbell, PhCalendarBlank, PhChartBar, PhWarningCircle } from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import {
  periodFromQuery,
  type AppliedPeriod,
  type PeriodLimits,
  type PeriodRange,
} from '../analytics-period'
import PeriodPicker from '../components/PeriodPicker.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { chartColors } from '../chart-theme'
import { createKcalFormatter, createNumberFormatter, i18n } from '../i18n'
import { isoDateInTimeZone } from '../date-format'
import { useAuthStore } from '../stores/auth'
import type { ReaderPeriodCapabilities } from '../types'

interface Weekday {
  weekday: number
  label: string
  count: number
  mean_kcal: number | null
  median_kcal: number | null
  p25_kcal: number | null
  p75_kcal: number | null
  mean_deviation_kcal: number | null
  mean_protein_g: number | null
}

const t = i18n.global.t.bind(i18n.global)
const locale = i18n.global.locale
const route = useRoute()
const router = useRouter()
const today = isoDateInTimeZone(useAuthStore().user?.timezone ?? 'UTC')
const limits: PeriodLimits = { minDays: 1, maxDays: 3661, customRange: true }
const readerCapability: ReaderPeriodCapabilities = {
  supports_all_data: false, all_data_start_date: null,
}
const allRange: PeriodRange | null = null
const initial = periodFromQuery(route.query, limits, today, allRange)
const start = ref(initial.start)
const end = ref(initial.end)
const period = ref(initial.period)
let latestLoadId = 0
let disposed = false
const weekdays = ref<Weekday[]>([])
const error = ref('')
const loading = ref(true)
const number = createNumberFormatter({ maximumFractionDigits: 0 })
const kcal = createKcalFormatter()

function applyPeriod(value: AppliedPeriod): void {
  const selected = periodFromQuery(value, limits, today, allRange)
  start.value = selected.start
  end.value = selected.end
  period.value = selected.period
  void load()
}

function weekdayLabel(day: number) {
  const keys = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
  return t(`weekdays.${keys[day]}`, { locale: locale.value })
}

function numericValue(value: number | null) {
  if (value == null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    if (nextStart === start.value && nextEnd === end.value && nextPeriod === period.value) return
    const selected = periodFromQuery(route.query, limits, today, allRange)
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    void load()
  },
)

async function load() {
  if (disposed) return
  const requestId = ++latestLoadId
  const request: AppliedPeriod = { start: start.value, end: end.value, period: period.value }
  error.value = ''
  loading.value = true
  try {
    await router.replace({
      query: { ...route.query, ...request },
      hash: route.hash,
    })
    if (requestId !== latestLoadId) return
    const params = new URLSearchParams({ start: request.start, end: request.end })
    const result = await api<{ weekdays: Weekday[] }>(`/analytics/weekdays?${params}`)
    if (requestId !== latestLoadId) return
    weekdays.value = result.weekdays.map((item) => ({
      ...item,
      count: Number(item.count),
      mean_kcal: numericValue(item.mean_kcal),
      median_kcal: numericValue(item.median_kcal),
      p25_kcal: numericValue(item.p25_kcal),
      p75_kcal: numericValue(item.p75_kcal),
      mean_deviation_kcal: numericValue(item.mean_deviation_kcal),
      mean_protein_g: numericValue(item.mean_protein_g),
    }))
  } catch (cause) {
    if (requestId !== latestLoadId) return
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    if (requestId === latestLoadId) loading.value = false
  }
}
onMounted(load)
onBeforeUnmount(() => {
  disposed = true
  latestLoadId += 1
})

const recorded = computed(() => weekdays.value.filter((item) => item.count > 0))
const highestDay = computed(() =>
  [...recorded.value].sort((a, b) => (b.mean_kcal ?? 0) - (a.mean_kcal ?? 0)).at(0) ?? null,
)
const lowestDay = computed(() =>
  [...recorded.value].sort((a, b) => (a.mean_kcal ?? 0) - (b.mean_kcal ?? 0)).at(0) ?? null,
)
const proteinDay = computed(() =>
  [...recorded.value].sort((a, b) => (b.mean_protein_g ?? 0) - (a.mean_protein_g ?? 0)).at(0) ?? null,
)
const totalDays = computed(() => recorded.value.reduce((sum, item) => sum + item.count, 0))

const option = computed<EChartsOption>(() => {
  const palette = chartColors.value
  return {
    backgroundColor: palette.background,
    animationDuration: 500,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
      valueFormatter: (value) => `${kcal.format(Number(value))} ${t('common.kcal')}`,
    },
    legend: {
      top: 0,
      right: 0,
      data: [t('weekdays.mean'), t('weekdays.median')],
      textStyle: { color: palette.label, fontFamily: 'Inter' },
    },
    grid: { left: 58, right: 18, top: 48, bottom: 38 },
    xAxis: {
      type: 'category',
      data: weekdays.value.map((item) => weekdayLabel(item.weekday).slice(0, 2)),
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { show: false },
      axisLabel: { color: palette.label, fontFamily: 'Inter' },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: palette.axis } },
      name: t('common.kcal'),
      nameTextStyle: { color: palette.label, fontFamily: 'Inter' },
      axisLabel: { color: palette.label, fontFamily: 'Inter', formatter: (value: number) => kcal.format(value) },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [
      {
        name: t('weekdays.mean'),
        type: 'bar',
        barMaxWidth: 42,
        data: weekdays.value.map((item) => item.mean_kcal),
        itemStyle: { color: palette.violet, borderRadius: [5, 5, 0, 0] },
      },
      {
        name: t('weekdays.median'),
        type: 'line',
        showSymbol: true,
        symbolSize: 7,
        data: weekdays.value.map((item) => item.median_kcal),
        lineStyle: { color: palette.teal, width: 2 },
        itemStyle: { color: palette.teal },
      },
    ],
  }
})

</script>

<template>
  <div class="page-heading analytics-page-heading">
    <div class="analytics-page-heading-content">
      <div><h1>{{ t('weekdays.title') }}</h1><p>{{ t('weekdays.description') }}</p></div>
      <PeriodPicker
        :start="start"
        :end="end"
        :period="period"
        :today="today"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :period-capabilities="readerCapability"
        :all-range="allRange"
        @apply="applyPeriod"
      />
    </div>
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('weekdays.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('weekdays.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.highest') }}</small><strong>{{ highestDay ? `${weekdayLabel(highestDay.weekday)} · ${kcal.format(highestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.lowest') }}</small><strong>{{ lowestDay ? `${weekdayLabel(lowestDay.weekday)} · ${kcal.format(lowestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhBarbell :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.mostProtein') }}</small><strong>{{ proteinDay?.mean_protein_g == null ? '–' : `${weekdayLabel(proteinDay.weekday)} · ${number.format(proteinDay.mean_protein_g)} g` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.dataBasis') }}</small><strong>{{ totalDays }} {{ t('common.days') }}</strong></span>
      </article>
    </section>
    <ChartPanel
      :title="t('weekdays.chartTitle')"
      :option="option"
      :empty="!recorded.length"
      :height="330"
    >
      <template #header-actions><span class="chart-range">{{ t('weekdays.chartRange') }}</span></template>
    </ChartPanel>
    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>{{ t('weekdays.tableTitle') }}</h2><p>{{ t('weekdays.tableDescription') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ t('common.day') }}</th><th class="number">{{ t('common.days') }}</th><th class="number">{{ t('weekdays.mean') }}</th><th class="number">{{ t('weekdays.median') }}</th><th class="number">{{ t('weekdays.middle50') }}</th><th class="number">{{ t('weekdays.averageDeviation') }}</th><th class="number">{{ t('charts.protein') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in weekdays" :key="item.weekday">
              <td><strong>{{ weekdayLabel(item.weekday) }}</strong></td>
              <td class="number">{{ item.count }}</td>
              <td class="number">{{ item.mean_kcal == null ? '–' : `${kcal.format(item.mean_kcal)} kcal` }}</td>
              <td class="number">{{ item.median_kcal == null ? '–' : `${kcal.format(item.median_kcal)} kcal` }}</td>
              <td class="number">{{ item.p25_kcal == null ? '–' : `${kcal.format(item.p25_kcal)}–${kcal.format(item.p75_kcal ?? 0)} kcal` }}</td>
              <td :class="['number', 'difference-value', (item.mean_deviation_kcal ?? 0) > 0 ? 'over' : 'under']">{{ item.mean_deviation_kcal == null ? '–' : `${item.mean_deviation_kcal > 0 ? '+' : ''}${kcal.format(item.mean_deviation_kcal)} kcal` }}</td>
              <td class="number">{{ item.mean_protein_g == null ? '–' : `${number.format(item.mean_protein_g)} g` }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
