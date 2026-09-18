<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { WeightResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const response = ref<WeightResponse | null>(null)
const loading = ref(true)
const error = ref('')

const latest = computed(() => response.value?.points.at(-1) ?? null)
const dateFormatter = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeZone: 'UTC' })

function formatDate(value: string): string {
  return dateFormatter.format(new Date(`${value}T12:00:00Z`))
}

function accountDate(now: Date): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: auth.user?.timezone ?? 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]))
  return `${values.year}-${values.month}-${values.day}`
}

function shiftDate(value: string, days: number): string {
  const date = new Date(`${value}T12:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

function formatWeight(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  const end = accountDate(new Date())
  const start = shiftDate(end, -30)
  const params = new URLSearchParams({ start, end })
  try {
    response.value = await api<WeightResponse>(`/analytics/weight?${params.toString()}`)
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'weight.loadFailed')
      : t('weight.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('weight.title') }}</h1>
      <p>{{ t('weight.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
  <div v-else-if="error" class="card error" role="alert">{{ error }}</div>
  <template v-else-if="response">
    <section class="metric-grid">
      <article class="card metric-card">
        <span class="metric-label">{{ t('weight.latest') }}</span>
        <strong v-if="latest">{{ formatWeight(latest.weight_kg) }} kg</strong>
        <strong v-else>–</strong>
        <small v-if="latest">{{ formatDate(latest.date) }}</small>
      </article>
      <article class="card metric-card">
        <span class="metric-label">{{ t('weight.provider') }}</span>
        <strong>{{ response.selected_provider ? t(`providerPreferencesUi.providers.${response.selected_provider.provider_key}`) : t('weight.noProvider') }}</strong>
        <small>{{ formatDate(response.start_date) }} – {{ formatDate(response.end_date) }}</small>
      </article>
    </section>
    <section class="card table-card">
      <h2>{{ t('weight.history') }}</h2>
      <p v-if="!response.points.length" class="table-secondary">{{ t('weight.empty') }}</p>
      <div v-else class="table-scroll">
        <table>
          <thead><tr><th>{{ t('weight.date') }}</th><th>{{ t('weight.value') }}</th></tr></thead>
          <tbody>
            <tr v-for="point in [...response.points].reverse()" :key="point.date">
              <td>{{ formatDate(point.date) }}</td>
              <td>{{ formatWeight(point.weight_kg) }} kg</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
