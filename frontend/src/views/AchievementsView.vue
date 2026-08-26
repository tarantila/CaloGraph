<script setup lang="ts">
import {
  PhArrowsClockwise,
  PhCalendarBlank,
  PhChartBar,
  PhChartLineUp,
  PhCheckCircle,
  PhDownloadSimple,
  PhFingerprint,
  PhFire,
  PhFile,
  PhHeart,
  PhLock,
  PhShieldCheck,
  PhTrophy,
  PhUploadSimple,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref, type Component } from 'vue'

import { api, localizeApiError } from '../api'
import { i18n } from '../i18n'
import type { Achievement, AchievementListResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const achievements = ref<Achievement[]>([])
const error = ref('')
const loading = ref(true)
const categoryOrder = ['usage', 'tracking', 'streak', 'activity', 'analytics', 'budget', 'sources', 'data_quality', 'export', 'import', 'security', 'hidden']
const groups = computed(() =>
  categoryOrder
    .map((category) => ({
      category,
      items: achievements.value.filter((item) => item.category === category),
    }))
    .filter((group) => group.items.length),
)

const iconMap: Record<string, Component> = {
  activity: PhChartLineUp,
  arrows: PhArrowsClockwise,
  calendar: PhCalendarBlank,
  chart: PhChartBar,
  download: PhDownloadSimple,
  fingerprint: PhFingerprint,
  flame: PhFire,
  heart: PhHeart,
  repeat: PhArrowsClockwise,
  shield: PhShieldCheck,
  table: PhFile,
  trophy: PhTrophy,
  upload: PhUploadSimple,
}

function iconFor(item: Achievement): Component {
  return iconMap[item.icon ?? 'trophy'] ?? PhTrophy
}
function isLockedHidden(item: Achievement): boolean {
  return item.placeholder === true || (item.hidden && !item.unlocked)
}
function label(item: Achievement): string {
  if (isLockedHidden(item)) return t('achievements.hiddenName')
  return item.key ? t(`achievements.names.${item.key}`) : t('achievements.hiddenName')
}
function description(item: Achievement): string {
  if (isLockedHidden(item)) return t('achievements.hiddenDescription')
  return item.key ? t(`achievements.descriptions.${item.key}`) : t('achievements.hiddenDescription')
}

function categoryLabel(category: string): string {
  return t(`achievements.categories.${category}`)
}

onMounted(async () => {
  try {
    achievements.value = (await api<AchievementListResponse>('/achievements')).achievements
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('achievements.title') }}</h1>
      <p>{{ t('achievements.description') }}</p>
    </div>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('achievements.loading') }}</div>
  <template v-else>
    <section v-for="group in groups" :key="group.category" class="achievement-section" :aria-labelledby="`achievement-${group.category}`">
      <div class="section-card-header">
        <div>
          <h2 :id="`achievement-${group.category}`">{{ categoryLabel(group.category) }}</h2>
        </div>
      </div>
      <div class="achievement-grid">
        <article
          v-for="(item, index) in group.items"
          :key="item.key ?? `${group.category}-${index}`"
          :class="['card', 'achievement-card', { unlocked: item.unlocked, hidden: isLockedHidden(item) }]"
        >
          <div class="achievement-card-icon">
            <PhCheckCircle v-if="item.unlocked" :size="22" weight="fill" />
            <PhLock v-else-if="isLockedHidden(item)" :size="22" weight="fill" />
            <component :is="iconFor(item)" v-else :size="22" weight="duotone" />
          </div>
          <div class="achievement-card-content">
            <h3><span v-if="isLockedHidden(item)" aria-hidden="true">??? </span>{{ label(item) }}</h3>
            <p>{{ description(item) }}</p>
            <div v-if="!item.unlocked && item.target != null" class="achievement-progress">
              <div class="achievement-progress-label">
                <span>{{ t('achievements.progress') }}</span>
                <strong>{{ item.progress ?? 0 }} / {{ item.target }}</strong>
              </div>
              <progress :value="item.progress ?? 0" :max="item.target" />
            </div>
            <span v-else-if="item.unlocked" class="achievement-unlocked">{{ t('achievements.unlocked') }}</span>
          </div>
        </article>
      </div>
    </section>
    <div v-if="!achievements.length" class="card empty">{{ t('achievements.noData') }}</div>
  </template>
</template>
