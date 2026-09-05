<script setup lang="ts">
import { PhCalendarBlank, PhChartBar, PhClockCounterClockwise, PhGauge } from '@phosphor-icons/vue'
import type { EChartsOption, LineSeriesOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { hasActivityCreditAmount } from '../activity'
import { api, localizeApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import { formatDate, formatDayMonth, shiftIsoDate } from '../date-format'
import { createKcalFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'

interface Week {
  week_start: string
  consumed_kcal: number
  budget_kcal: number | null
  deviation_kcal: number | null
  remaining_kcal: number | null
  activity_credit_kcal: number
  effective_budget_kcal: number | null
  effective_deviation_kcal: number | null
  effective_remaining_kcal: number | null
  mean_kcal: number | null
  median_kcal: number | null
}

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const locale = i18n.global.locale
const weeks = ref<Week[]>([])
const highlightOverBudget = ref(true)
const error = ref('')
const loading = ref(true)
const kcal = createKcalFormatter()
const weekdayKeys = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const
const weekStart = computed(() => auth.user?.week_starts_on ?? 0)
const weeklyRange = computed(() => {
  const start = weekStart.value
  const end = (start + 6) % 7
  const label = (day: number) => t(`weekdays.${weekdayKeys[day]}`, { locale: locale.value })
  return t('weekly.range', { start: label(start), end: label(end), locale: locale.value })
})

onMounted(async () => {
  try {
    weeks.value = (await api<{ weeks: Week[] }>('/analytics/weekly')).weeks
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
})

const latestWeek = computed(() => weeks.value.at(-1) ?? null)
const recordedWeeks = computed(() => weeks.value.filter((week) => week.mean_kcal != null))
const averageWeek = computed(() =>
  recordedWeeks.value.length
    ? recordedWeeks.value.reduce((sum, week) => sum + week.consumed_kcal, 0) / recordedWeeks.value.length
    : null,
)
const weekHasActivityCredit = (week: Week) =>
  hasActivityCreditAmount(week.activity_credit_kcal)
const activityRelevant = computed(() => weeks.value.some(weekHasActivityCredit))
const deviationForWeek = (week: Week) =>
  weekHasActivityCredit(week) ? week.effective_deviation_kcal : week.deviation_kcal
const remainingForWeek = (week: Week) =>
  weekHasActivityCredit(week) ? week.effective_remaining_kcal : week.remaining_kcal
const weeksWithinBudget = computed(() =>
  recordedWeeks.value.filter((week) => {
    const deviation = deviationForWeek(week)
    return deviation != null && deviation <= 0
  }).length,
)

const option = computed<EChartsOption>(() => {
  const budgetSeries: LineSeriesOption[] = activityRelevant.value
    ? [
        {
          name: t('activity.baseBudget'),
          type: 'line' as const,
          showSymbol: false,
          data: weeks.value.map((week) => week.budget_kcal),
          lineStyle: { color: '#64748b', width: 2, type: 'dashed' },
          itemStyle: { color: '#64748b' },
        },
        {
          name: t('activity.effectiveBudget'),
          type: 'line' as const,
          showSymbol: false,
          data: weeks.value.map((week) =>
            weekHasActivityCredit(week) ? week.effective_budget_kcal : null,
          ),
          lineStyle: { color: '#fb923c', width: 2 },
          itemStyle: { color: '#fb923c' },
        },
      ]
    : [
        {
          name: t('weekly.budgetTable'),
          type: 'line' as const,
          showSymbol: false,
          data: weeks.value.map((week) => week.budget_kcal),
          lineStyle: { color: '#64748b', width: 2 },
          itemStyle: { color: '#64748b' },
        },
      ]
  return {
    animationDuration: 500,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111d30',
      borderColor: '#324157',
      textStyle: { color: '#f3f6fb' },
      valueFormatter: (value) => `${kcal.format(Number(value))} ${t('common.kcal')}`,
    },
    legend: {
      top: 0,
      right: 0,
      data: [t('charts.intake'), ...budgetSeries.map((series) => String(series.name))],
    },
    grid: { left: 58, right: 18, top: 48, bottom: 40 },
    xAxis: {
      type: 'category',
      data: weeks.value.map((week) => formatDayMonth(week.week_start)),
      axisLine: { lineStyle: { color: '#263449' } },
      axisTick: { show: false },
      axisLabel: { color: '#98a5b9', fontFamily: 'Inter' },
    },
    yAxis: {
      type: 'value',
      name: t('common.kcal'),
      nameTextStyle: { color: '#98a5b9', fontFamily: 'Inter' },
      axisLabel: { color: '#98a5b9', fontFamily: 'Inter', formatter: (value: number) => kcal.format(value) },
      splitLine: { lineStyle: { color: '#263449', type: 'dashed' } },
    },
    series: [
      {
        name: t('charts.intake'),
        type: 'bar',
        barMaxWidth: 34,
        data: weeks.value.map((week) =>
          highlightOverBudget.value && deviationForWeek(week) != null && deviationForWeek(week)! > 0
            ? { value: week.consumed_kcal, itemStyle: { color: '#fb7185' } }
            : week.consumed_kcal,
        ),
        itemStyle: { color: '#8b5cf6', borderRadius: [5, 5, 0, 0] },
      },
      ...budgetSeries,
    ],
  }
})

function weekLabel(value: string) {
  return `${formatDayMonth(value)} – ${formatDate(shiftIsoDate(value, 6))}`
}
</script>
<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('weekly.title') }}</h1>
      <p>{{ t('weekly.description', { range: weeklyRange }) }}</p>
    </div>
    <span class="page-context">{{ t('weekly.latestWeeks', { count: weeks.length }) }}</span>
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading" aria-live="polite">{{ t('common.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('weekly.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekly.currentWeek') }}</small><strong>{{ latestWeek ? `${kcal.format(latestWeek.consumed_kcal)} kcal` : '–' }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhGauge :size="20" weight="duotone" /></span>
        <span><small>{{ latestWeek && weekHasActivityCredit(latestWeek) ? t('activity.effectiveRemaining') : t('overview.weekRemaining') }}</small><strong>{{ latestWeek == null || remainingForWeek(latestWeek) == null ? '–' : `${kcal.format(Math.max(remainingForWeek(latestWeek)!, 0))} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhClockCounterClockwise :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekly.averagePerWeek') }}</small><strong>{{ averageWeek == null ? '–' : `${kcal.format(averageWeek)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('weekly.withinBudget') }}</small><strong>{{ weeksWithinBudget }} {{ t('common.of') }} {{ recordedWeeks.length }}</strong></span>
      </article>
    </section>
    <ChartPanel
      :title="t('charts.intake') + ' & ' + (activityRelevant ? t('activity.effectiveBudget') : t('weekly.budgetTable'))"
      :option="option"
      :empty="!recordedWeeks.length"
      :height="330"
    >
      <template #header-actions>
        <div class="chart-header-actions">
          <label class="chart-highlight-toggle">
            <input v-model="highlightOverBudget" type="checkbox" role="switch" />
            <span>{{ t('charts.highlightOverBudget') }}</span>
          </label>
          <span class="chart-range">{{ weeklyRange }}</span>
        </div>
      </template>
    </ChartPanel>
    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>{{ t('weekly.detail') }}</h2><p>{{ t('weekly.detailDescription') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ weeklyRange }}</th><th class="number">{{ t('charts.intake') }}</th><th class="number">{{ activityRelevant ? t('activity.baseBudget') : t('weekly.budgetTable') }}</th><template v-if="activityRelevant"><th class="number">{{ t('activity.activityCredit') }}</th><th class="number">{{ t('activity.effectiveBudget') }}</th></template><th class="number">{{ t('common.deviation') }}</th><th class="number">{{ t('daily.average') }}</th><th class="number">{{ t('weekly.median') }}</th></tr></thead>
          <tbody>
            <tr v-for="week in [...weeks].reverse()" :key="week.week_start">
              <td><strong>{{ weekLabel(week.week_start) }}</strong></td>
              <td class="number">{{ kcal.format(week.consumed_kcal) }} kcal</td>
              <td class="number">{{ week.budget_kcal == null ? '–' : `${kcal.format(week.budget_kcal)} kcal` }}</td>
              <template v-if="activityRelevant">
                <td class="number">{{ weekHasActivityCredit(week) ? `+${kcal.format(week.activity_credit_kcal)} kcal` : '–' }}</td>
                <td class="number">{{ !weekHasActivityCredit(week) || week.effective_budget_kcal == null ? '–' : `${kcal.format(week.effective_budget_kcal)} kcal` }}</td>
              </template>
              <td :class="['number', 'difference-value', deviationForWeek(week) == null ? null : deviationForWeek(week)! > 0 ? 'over' : 'under']">
                <template v-if="deviationForWeek(week) != null">{{ deviationForWeek(week)! > 0 ? '+' : '' }}{{ kcal.format(deviationForWeek(week)!) }} kcal</template>
                <template v-else>–</template>
              </td>
              <td class="number">{{ week.mean_kcal == null ? '–' : `${kcal.format(week.mean_kcal)} kcal` }}</td>
              <td class="number">{{ week.median_kcal == null ? '–' : `${kcal.format(week.median_kcal)} kcal` }}</td>
            </tr>
            <tr v-if="!weeks.length"><td :colspan="activityRelevant ? 8 : 6" class="empty">{{ t('weekly.noWeeks') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
