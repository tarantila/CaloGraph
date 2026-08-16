<script setup lang="ts">
import { PhCheckCircle, PhLock, PhTrophy } from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { api, localizeApiError } from '../api'
import { i18n } from '../i18n'
import type { Achievement, AchievementListResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const achievements = ref<Achievement[]>([])
const error = ref('')
const loading = ref(true)

const categoryOrder = ['tracking', 'streak', 'sources', 'data_quality', 'hidden']
const groups = computed(() =>
  categoryOrder
    .map((category) => ({
      category,
      items: achievements.value.filter((item) => item.category === category),
    }))
    .filter((group) => group.items.length),
)

function label(item: Achievement): string {
  return item.hidden && !item.unlocked ? t('achievements.hiddenName') : t(`achievements.names.${item.key}`)
}

function description(item: Achievement): string {
  if (item.hidden && !item.unlocked) return t('achievements.hiddenDescription')
  return t(`achievements.descriptions.${item.key}`)
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
          v-for="item in group.items"
          :key="item.key"
          :class="['card', 'achievement-card', { unlocked: item.unlocked, hidden: item.hidden && !item.unlocked }]"
        >
          <div class="achievement-card-icon">
            <PhCheckCircle v-if="item.unlocked" :size="22" weight="fill" />
            <PhLock v-else-if="item.hidden" :size="22" weight="fill" />
            <PhTrophy v-else :size="22" weight="duotone" />
          </div>
          <div class="achievement-card-content">
            <h3>{{ label(item) }}</h3>
            <p>{{ description(item) }}</p>
            <div v-if="!item.unlocked && item.target !== null" class="achievement-progress">
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
