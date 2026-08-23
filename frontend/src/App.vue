<script setup lang="ts">
import {
  PhCalendarBlank,
  PhChartBar,
  PhChartLineUp,
  PhDatabase,
  PhDownloadSimple,
  PhList,
  PhListBullets,
  PhSignOut,
  PhSquaresFour,
  PhTarget,
  PhTrophy,
  PhUserCircle,
  PhX,
} from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { i18n } from './i18n'
import type { Achievement } from './types'
import { APP_VERSION } from './version'
import { useAuthStore } from './stores/auth'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const usesFocusedLayout = computed(
  () => route.meta.public === true || route.meta.onboarding === true,
)

const primaryNavigation = [
  { to: { name: 'overview' }, label: 'navigation.overview', icon: PhSquaresFour },
  { to: { name: 'daily' }, label: 'navigation.daily', icon: PhListBullets },
  { to: { name: 'weekly' }, label: 'navigation.weekly', icon: PhChartBar },
  { to: { name: 'weekdays' }, label: 'navigation.weekdays', icon: PhCalendarBlank },
  { to: { name: 'calendar' }, label: 'navigation.calendar', icon: PhCalendarBlank },
  { to: { name: 'trends' }, label: 'navigation.trends', icon: PhChartLineUp },
  { to: { name: 'micronutrients' }, label: 'navigation.micronutrients', icon: PhChartBar },
  { to: { name: 'achievements' }, label: 'navigation.achievements', icon: PhTrophy },
]

const utilityNavigation = [
  { to: { name: 'imports' }, label: 'navigation.imports', icon: PhDownloadSimple },
  { to: { name: 'quality' }, label: 'navigation.quality', icon: PhDatabase },
  { to: { name: 'targets' }, label: 'navigation.targets', icon: PhTarget },
  { to: { name: 'account' }, label: 'navigation.account', icon: PhUserCircle },
]

type NavigationItem = (typeof primaryNavigation)[number] | (typeof utilityNavigation)[number]

function isNavActive(item: NavigationItem) {
  return item.to.name === route.name
}

async function signOut() {
  await auth.logout()
  await router.push({ name: 'login' })
}

async function retrySessionRestore() {
  auth.clearSessionRestoreError()
  const currentPath =
    typeof window === 'undefined'
      ? router.currentRoute.value.fullPath
      : `${window.location.pathname}${window.location.search}${window.location.hash}`
  await router.replace(currentPath)
}

function hasAchievementKey(item: Achievement): item is Achievement & { key: string } {
  return typeof item.key === 'string'
}

const newlyUnlockedAchievements = computed(() => auth.newlyUnlockedAchievements.filter(hasAchievementKey))

function achievementLabel(key: string): string {
  return t(`achievements.names.${key}`)
}

function dismissAchievementNotice(): void {
  auth.clearAchievementNotice()
}

</script>

<template>
  <main v-if="auth.sessionRestoreUnavailable && !auth.user" class="login-page">
    <section class="card login-card" aria-labelledby="connection-error-title">
      <h1 id="connection-error-title">{{ t('errors.connectionTitle') }}</h1>
      <p role="alert">{{ t('errors.connectionFailed') }}</p>
      <button class="button" type="button" @click="retrySessionRestore">{{ t('common.tryAgain') }}</button>
    </section>
  </main>
  <RouterView v-else-if="usesFocusedLayout" />
  <div v-else class="app-shell">
    <div v-if="newlyUnlockedAchievements.length" class="achievement-toast" role="status">
      <div>
        <strong>{{ t('achievements.unlockedTitle') }}</strong>
        <span v-for="item in newlyUnlockedAchievements" :key="item.key">
          {{ achievementLabel(item.key) }}
        </span>
      </div>
      <button type="button" :aria-label="t('common.close')" @click="dismissAchievementNotice">
        <PhX :size="18" aria-hidden="true" />
      </button>
    </div>
    <aside :class="['sidebar', { open: menuOpen }]" :aria-label="t('navigation.aria')">
      <RouterLink class="brand" :to="{ name: 'overview' }" @click="menuOpen = false">
        <img class="brand-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <strong>CaloGraph</strong>
      </RouterLink>
      <nav class="sidebar-primary-navigation" :aria-label="t('navigation.primaryAria')">
        <RouterLink
          v-for="item in primaryNavigation"
          :key="item.label"
          :class="{ active: isNavActive(item) }"
          :to="item.to"
          @click="menuOpen = false"
        >
          <component :is="item.icon" :size="20" weight="regular" aria-hidden="true" />
          {{ t(item.label) }}
        </RouterLink>
      </nav>
      <div class="sidebar-lower">
        <nav class="sidebar-utility-navigation" :aria-label="t('navigation.utilityAria')">
          <RouterLink
            v-for="item in utilityNavigation"
            :key="item.label"
            :class="{ active: isNavActive(item) }"
            :to="item.to"
            @click="menuOpen = false"
          >
            <component :is="item.icon" :size="20" weight="regular" aria-hidden="true" />
            {{ t(item.label) }}
          </RouterLink>
        </nav>
        <div class="sidebar-footer">
          <small>CaloGraph v{{ APP_VERSION }}</small>
          <button class="sidebar-signout" type="button" @click="signOut">
            <PhSignOut :size="18" aria-hidden="true" />
            {{ t('navigation.logout') }}
          </button>
        </div>
      </div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <button class="menu-button" type="button" :aria-label="menuOpen ? t('navigation.close') : t('navigation.open')" @click="menuOpen = !menuOpen">
          <PhX v-if="menuOpen" :size="24" aria-hidden="true" />
          <PhList v-else :size="24" aria-hidden="true" />
        </button>
        <RouterLink class="mobile-brand" :to="{ name: 'overview' }" @click="menuOpen = false">
          <img class="mobile-brand-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
          <strong>CaloGraph</strong>
        </RouterLink>
      </header>
      <main id="main-content" class="page"><RouterView /></main>
    </div>
  </div>
</template>
