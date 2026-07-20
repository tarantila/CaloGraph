<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const isLogin = computed(() => route.name === 'login')

const navigation = [
  ['overview', 'Übersicht', '◫'],
  ['daily', 'Tagesverlauf', '≡'],
  ['weekly', 'Wochenbudget', '▥'],
  ['weekdays', 'Wochentage', '▤'],
  ['trends', 'Trends', '⌁'],
  ['calendar', 'Kalender', '□'],
  ['quality', 'Datenqualität', '◇'],
  ['imports', 'Importe', '⇩'],
  ['settings', 'Einstellungen', '⚙'],
]

async function signOut() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <RouterView v-if="isLogin" />
  <div v-else class="app-shell">
    <aside :class="['sidebar', { open: menuOpen }]" aria-label="Hauptnavigation">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">C</span>
        <div><strong>CaloGraph</strong><small>Health analytics</small></div>
      </div>
      <nav>
        <RouterLink
          v-for="item in navigation"
          :key="item[0]"
          :to="{ name: item[0] }"
          @click="menuOpen = false"
        >
          <span aria-hidden="true">{{ item[2] }}</span>{{ item[1] }}
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
        <span>{{ auth.user?.username }}</span>
        <button class="text-button" type="button" @click="signOut">Abmelden</button>
      </div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <button class="menu-button" type="button" aria-label="Navigation öffnen" @click="menuOpen = !menuOpen">☰</button>
        <span class="privacy-label">Privat · lokal betrieben</span>
        <span class="timezone">{{ auth.user?.timezone }}</span>
      </header>
      <main id="main-content" class="page"><RouterView /></main>
    </div>
  </div>
</template>

