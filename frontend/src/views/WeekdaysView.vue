<script setup lang="ts">
import { PhBarbell, PhCalendarBlank, PhChartBar, PhWarningCircle } from '@phosphor-icons/vue'
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import DateFilter from '../components/DateFilter.vue'
import { createNumberFormatter, i18n } from '../i18n'

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
const currentDate = new Date()
const defaultStartDate = new Date(currentDate)
defaultStartDate.setDate(currentDate.getDate() - 179)
const iso = (value: Date) => {
  const year = value.getFullYear()
  const month = `${value.getMonth() + 1}`.padStart(2, '0')
  const day = `${value.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}
const start = ref(String(route.query.start ?? iso(defaultStartDate)))
const end = ref(String(route.query.end ?? iso(currentDate)))
const weekdays = ref<Weekday[]>([])
const error = ref('')
const loading = ref(true)
const number = createNumberFormatter({ maximumFractionDigits: 0 })

function weekdayLabel(day: number) {
  const keys = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
  return t(`weekdays.${keys[day]}`, { locale: locale.value })
}

function numericValue(value: number | null) {
  if (value == null) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

async function load() {
  error.value = ''
  loading.value = true
  await router.replace({ query: { start: start.value, end: end.value } })
  try {
    const result = await api<{ weekdays: Weekday[] }>(`/analytics/weekdays?start=${start.value}&end=${end.value}`)
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
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}
onMounted(load)

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

const option = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#111d30',
    borderColor: '#324157',
    textStyle: { color: '#f3f6fb' },
    valueFormatter: (value) => `${number.format(Number(value))} ${t('common.kcal')}`,
  },
  legend: {
    top: 0,
    right: 0,
    data: [t('weekdays.mean'), t('weekdays.median')],
    textStyle: { color: '#98a5b9', fontFamily: 'Inter' },
  },
  grid: { left: 58, right: 18, top: 48, bottom: 38 },
  xAxis: {
    type: 'category',
    data: weekdays.value.map((item) => weekdayLabel(item.weekday).slice(0, 2)),
    axisLine: { lineStyle: { color: '#263449' } },
    axisTick: { show: false },
    axisLabel: { color: '#98a5b9', fontFamily: 'Inter' },
  },
  yAxis: {
    type: 'value',
    name: t('common.kcal'),
    nameTextStyle: { color: '#98a5b9', fontFamily: 'Inter' },
    axisLabel: { color: '#98a5b9', fontFamily: 'Inter' },
    splitLine: { lineStyle: { color: '#263449', type: 'dashed' } },
  },
  series: [
    {
      name: t('weekdays.mean'),
      type: 'bar',
      barMaxWidth: 42,
      data: weekdays.value.map((item) => item.mean_kcal),
      itemStyle: { color: '#8b5cf6', borderRadius: [5, 5, 0, 0] },
    },
    {
      name: t('weekdays.median'),
      type: 'line',
      showSymbol: true,
      symbolSize: 7,
      data: weekdays.value.map((item) => item.median_kcal),
      lineStyle: { color: '#2dd4bf', width: 2 },
      itemStyle: { color: '#2dd4bf' },
    },
  ],
}))

</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('weekdays.title') }}</h1><p>{{ t('weekdays.description') }}</p></div>
    <DateFilter v-model:start="start" v-model:end="end" @apply="load" />
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('weekdays.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('weekdays.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.highest') }}</small><strong>{{ highestDay ? `${weekdayLabel(highestDay.weekday)} · ${number.format(highestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekdays.lowest') }}</small><strong>{{ lowestDay ? `${weekdayLabel(lowestDay.weekday)} · ${number.format(lowestDay.mean_kcal ?? 0)} kcal` : '–' }}</strong></span>
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
              <td class="number">{{ item.mean_kcal == null ? '–' : `${number.format(item.mean_kcal)} kcal` }}</td>
              <td class="number">{{ item.median_kcal == null ? '–' : `${number.format(item.median_kcal)} kcal` }}</td>
              <td class="number">{{ item.p25_kcal == null ? '–' : `${number.format(item.p25_kcal)}–${number.format(item.p75_kcal ?? 0)} kcal` }}</td>
              <td :class="['number', 'difference-value', (item.mean_deviation_kcal ?? 0) > 0 ? 'over' : 'under']">{{ item.mean_deviation_kcal == null ? '–' : `${item.mean_deviation_kcal > 0 ? '+' : ''}${number.format(item.mean_deviation_kcal)} kcal` }}</td>
              <td class="number">{{ item.mean_protein_g == null ? '–' : `${number.format(item.mean_protein_g)} g` }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
