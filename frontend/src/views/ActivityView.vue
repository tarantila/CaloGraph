<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api, localizeApiError } from '../api'
import { formatDate, formatWeekday, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type {
  VerificationActivityResponse,
  VerificationView,
} from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const initialEndDate = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const startDate = ref(shiftIsoDate(initialEndDate, -6))
const endDate = ref(initialEndDate)
const view = ref<VerificationView>('canonical')
const response = ref<VerificationActivityResponse | null>(null)
const loading = ref(true)
const error = ref('')
const energy = createNumberFormatter({ maximumFractionDigits: 1 })

const rangeLabel = computed(() => `${formatDate(startDate.value)} – ${formatDate(endDate.value)}`)
const hasActivityData = computed(() => response.value?.days.some((day) =>
  day.canonical?.status === 'available' || day.providers.some((record) => record.status === 'available'),
) ?? false)
const hasDays = computed(() => Boolean(response.value?.days.length))

function sourceTypeLabel(sourceType: string) {
  const key = `activity.source.${sourceType}`
  const label = t(key)
  return label === key ? sourceType : label
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({
    start: startDate.value,
    end: endDate.value,
    view: view.value,
  })
  try {
    response.value = await api<VerificationActivityResponse>(
      `/analytics/verification/activity?${params.toString()}`,
    )
  } catch (cause) {
    error.value = localizeApiError(cause, 'activityVerification.loadFailed')
  } finally {
    loading.value = false
  }
}

function selectView(nextView: VerificationView): void {
  if (view.value === nextView) return
  view.value = nextView
  void load()
}

function shiftRange(days: number): void {
  startDate.value = shiftIsoDate(startDate.value, days)
  endDate.value = shiftIsoDate(endDate.value, days)
  void load()
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('activityVerification.title') }}</h1>
      <p>{{ t('activityVerification.description') }}</p>
    </div>
  </div>

  <section class="verification-toolbar" :aria-label="t('activityVerification.title')">
    <div class="verification-view-toggle" role="group" :aria-label="t('activityVerification.title')">
      <button
        type="button"
        :aria-pressed="view === 'canonical'"
        :disabled="loading"
        @click="selectView('canonical')"
      >
        {{ t('activityVerification.canonicalToggle') }}
      </button>
      <button
        type="button"
        :aria-pressed="view === 'all'"
        :disabled="loading"
        @click="selectView('all')"
      >
        {{ t('activityVerification.allSourcesToggle') }}
      </button>
    </div>

    <div class="verification-date-navigation" role="group" :aria-label="t('common.period')">
      <button
        type="button"
        :aria-label="t('activityVerification.previousWeek')"
        :disabled="loading"
        @click="shiftRange(-7)"
      >
        <span aria-hidden="true">‹</span>
      </button>
      <strong>{{ rangeLabel }}</strong>
      <button
        type="button"
        :aria-label="t('activityVerification.nextWeek')"
        :disabled="loading"
        @click="shiftRange(7)"
      >
        <span aria-hidden="true">›</span>
      </button>
    </div>
  </section>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('activityVerification.loading') }}
  </div>
  <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
    <p>{{ error }}</p>
    <button class="button secondary" type="button" @click="load">{{ t('common.tryAgain') }}</button>
  </section>
  <section v-else-if="response" class="verification-provider-groups">
    <div v-if="!hasActivityData" class="card empty" role="status" aria-live="polite">
      {{ t('activityVerification.empty') }}
    </div>
    <section v-if="hasDays" class="card table-card verification-provider-group">
      <div class="verification-provider-group-header">
        <div>
          <h2>{{ view === 'canonical' ? t('verification.canonical') : t('verification.allSources') }}</h2>
          <p>{{ rangeLabel }}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{{ t('common.date') }}</th>
              <th>{{ t('verification.provider') }}</th>
              <th>{{ t('common.status') }}</th>
              <th>{{ t('verification.records', { count: '' }) }}</th>
              <th>{{ t('activityVerification.activeEnergy') }}</th>
              <th>{{ t('verification.sourceTypes') }}</th>
            </tr>
          </thead>
          <tbody v-if="view === 'canonical'">
            <template v-for="day in response.days" :key="day.date">
              <tr v-if="day.canonical">
                <td>{{ formatWeekday(day.date) }}, {{ formatDate(day.date) }}</td>
                <td><strong>{{ t(`verification.providers.${day.canonical.provider_key}`) }}</strong></td>
                <td>{{ t(`verification.status.${day.canonical.status}`) }}</td>
                <td>{{ t('verification.records', { count: day.canonical.record_count }) }}</td>
                <td>{{ day.canonical.active_energy_kcal == null ? '–' : `${energy.format(day.canonical.active_energy_kcal)} ${t('common.kcal')}` }}</td>
                <td>{{ day.canonical.source_types.map(sourceTypeLabel).join(', ') || '–' }}</td>
              </tr>
              <tr v-else>
                <td>{{ formatWeekday(day.date) }}, {{ formatDate(day.date) }}</td>
                <td colspan="5">{{ t('verification.status.no_data') }}</td>
              </tr>
            </template>
          </tbody>
          <tbody v-else>
            <template v-for="day in response.days" :key="day.date">
              <tr v-if="!day.providers.length">
                <td>{{ formatWeekday(day.date) }}, {{ formatDate(day.date) }}</td>
                <td colspan="5">{{ t('verification.status.no_data') }}</td>
              </tr>
              <tr v-for="record in day.providers" :key="`${day.date}-${record.provider_key}`">
                <td>{{ formatWeekday(day.date) }}, {{ formatDate(day.date) }}</td>
                <td><strong>{{ t(`verification.providers.${record.provider_key}`) }}</strong></td>
                <td>{{ t(`verification.status.${record.status}`) }}</td>
                <td>{{ t('verification.records', { count: record.record_count }) }}</td>
                <td>{{ record.active_energy_kcal == null ? '–' : `${energy.format(record.active_energy_kcal)} ${t('common.kcal')}` }}</td>
                <td>{{ record.source_types.map(sourceTypeLabel).join(', ') || '–' }}</td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
