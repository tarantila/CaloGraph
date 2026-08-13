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
  PhUserCircle,
  PhX,
} from '@phosphor-icons/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { i18n } from './i18n'
import { useAuthStore } from './stores/auth'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const usesFocusedLayout = computed(
  () => route.meta.public === true || route.meta.onboarding === true,
)

const navigation = [
  { to: { name: 'overview' }, label: 'navigation.overview', icon: PhSquaresFour },
  { to: { name: 'daily' }, label: 'navigation.daily', icon: PhListBullets },
  { to: { name: 'weekly' }, label: 'navigation.weekly', icon: PhChartBar },
  { to: { name: 'weekdays' }, label: 'navigation.weekdays', icon: PhCalendarBlank },
  { to: { name: 'calendar' }, label: 'navigation.calendar', icon: PhCalendarBlank },
  { to: { name: 'trends' }, label: 'navigation.trends', icon: PhChartLineUp },
  { to: { name: 'micronutrients' }, label: 'navigation.micronutrients', icon: PhChartBar },
  { to: { name: 'quality' }, label: 'navigation.quality', icon: PhDatabase },
  { to: { name: 'imports' }, label: 'navigation.imports', icon: PhDownloadSimple },
  { to: { name: 'targets' }, label: 'navigation.targets', icon: PhTarget },
  { to: { name: 'account' }, label: 'navigation.account', icon: PhUserCircle },
]

function isNavActive(item: (typeof navigation)[number]) {
  return item.to.name === route.name
}

async function signOut() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <RouterView v-if="usesFocusedLayout" />
  <div v-else class="app-shell">
    <aside :class="['sidebar', { open: menuOpen }]" :aria-label="t('navigation.aria')">
      <div class="brand">
        <img class="brand-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <strong>CaloGraph</strong>
      </div>
      <nav>
        <RouterLink
          v-for="item in navigation"
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
        <small>CaloGraph v0.3.4</small>
        <button class="sidebar-signout" type="button" @click="signOut">
          <PhSignOut :size="18" aria-hidden="true" />
          {{ t('navigation.logout') }}
        </button>
      </div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <button class="menu-button" type="button" :aria-label="menuOpen ? t('navigation.close') : t('navigation.open')" @click="menuOpen = !menuOpen">
          <PhX v-if="menuOpen" :size="24" aria-hidden="true" />
          <PhList v-else :size="24" aria-hidden="true" />
        </button>
        <div class="mobile-brand" aria-label="CaloGraph">
          <img class="mobile-brand-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
          <strong>CaloGraph</strong>
        </div>
      </header>
      <main id="main-content" class="page"><RouterView /></main>
    </div>
  </div>
</template>
