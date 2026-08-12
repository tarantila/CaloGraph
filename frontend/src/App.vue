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

import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const usesFocusedLayout = computed(
  () => route.meta.public === true || route.meta.onboarding === true,
)

const navigation = [
  { to: { name: 'overview' }, label: 'Übersicht', icon: PhSquaresFour },
  { to: { name: 'daily' }, label: 'Tagesverlauf', icon: PhListBullets },
  { to: { name: 'weekly' }, label: 'Wochen', icon: PhChartBar },
  { to: { name: 'weekdays' }, label: 'Wochentage', icon: PhCalendarBlank },
  { to: { name: 'calendar' }, label: 'Kalender', icon: PhCalendarBlank },
  { to: { name: 'trends' }, label: 'Trends', icon: PhChartLineUp },
  { to: { name: 'micronutrients' }, label: 'Mikronährstoffe', icon: PhChartBar },
  { to: { name: 'quality' }, label: 'Datenstatus', icon: PhDatabase },
  { to: { name: 'imports' }, label: 'Datenimport', icon: PhDownloadSimple },
  { to: { name: 'targets' }, label: 'Budgets & Ziele', icon: PhTarget },
  { to: { name: 'account' }, label: 'Konto', icon: PhUserCircle },
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
    <aside :class="['sidebar', { open: menuOpen }]" aria-label="Hauptnavigation">
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
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
        <small>CaloGraph v0.3.2</small>
        <button class="sidebar-signout" type="button" @click="signOut">
          <PhSignOut :size="18" aria-hidden="true" />
          Abmelden
        </button>
      </div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <button class="menu-button" type="button" :aria-label="menuOpen ? 'Navigation schließen' : 'Navigation öffnen'" @click="menuOpen = !menuOpen">
          <PhX v-if="menuOpen" :size="24" aria-hidden="true" />
          <PhList v-else :size="24" aria-hidden="true" />
        </button>
        <span class="privacy-label">Privat · lokal betrieben</span>
        <span class="timezone">{{ auth.user?.timezone }}</span>
      </header>
      <main id="main-content" class="page"><RouterView /></main>
    </div>
  </div>
</template>
