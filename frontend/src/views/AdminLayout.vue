<script setup lang="ts">
import {
  PhArchive,
  PhDatabase,
  PhFileText,
  PhList,
  PhSquaresFour,
  PhUserCircle,
  PhUsers,
} from '@phosphor-icons/vue'
import { nextTick, ref, watch } from 'vue'

import { useRoute } from 'vue-router'

import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const adminNavigation = ref<HTMLElement | null>(null)

const managementNavigation = [
  { name: 'admin-users', label: 'adminUi.users', icon: PhUsers },
  { name: 'admin-invitations', label: 'adminUi.invitations', icon: PhUserCircle },
  { name: 'admin-audit', label: 'adminUi.audit', icon: PhList },
] as const

const operationsNavigation = [
  { name: 'admin-logs', label: 'adminUi.logs', icon: PhFileText },
  { name: 'admin-backups', label: 'adminUi.backups', icon: PhArchive },
] as const

function isActive(name: string) {
  return route.name === name
}

watch(
  () => route.fullPath,
  async () => {
    await nextTick()
    const activeLink = adminNavigation.value?.querySelector<HTMLElement>('a.active')
    if (activeLink && typeof activeLink.scrollIntoView === 'function') {
      activeLink.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="admin-console" aria-labelledby="admin-console-title">
    <aside class="admin-sidebar" :aria-label="t('adminNav.title')">
      <h1 id="admin-console-title">{{ t('adminNav.title') }}</h1>
      <nav ref="adminNavigation" class="admin-sidebar-nav" :aria-label="t('adminNav.title')" tabindex="0">
        <RouterLink
          :class="{ active: isActive('admin-overview') }"
          :to="{ name: 'admin-overview' }"
          :aria-current="isActive('admin-overview') ? 'page' : undefined"
        >
          <PhSquaresFour :size="18" aria-hidden="true" />
          {{ t('adminUi.overview') }}
        </RouterLink>
        <p class="admin-sidebar-label">{{ t('adminNav.management') }}</p>
        <RouterLink
          v-for="item in managementNavigation"
          :key="item.name"
          :class="{ active: isActive(item.name) }"
          :to="{ name: item.name }"
          :aria-current="isActive(item.name) ? 'page' : undefined"
        >
          <component :is="item.icon" :size="18" aria-hidden="true" />
          {{ t(item.label) }}
        </RouterLink>
        <p class="admin-sidebar-label">{{ t('adminNav.systemGroup') }}</p>
        <RouterLink
          :class="{ active: isActive('admin-system') }"
          :to="{ name: 'admin-system' }"
          :aria-current="isActive('admin-system') ? 'page' : undefined"
        >
          <PhDatabase :size="18" aria-hidden="true" />
          {{ t('adminNav.system') }}
        </RouterLink>
        <RouterLink
          v-for="item in operationsNavigation"
          :key="item.name"
          :class="{ active: isActive(item.name) }"
          :to="{ name: item.name }"
          :aria-current="isActive(item.name) ? 'page' : undefined"
        >
          <component :is="item.icon" :size="18" aria-hidden="true" />
          {{ t(item.label) }}
        </RouterLink>
      </nav>
    </aside>
    <div class="admin-content"><RouterView /></div>
  </section>
</template>
