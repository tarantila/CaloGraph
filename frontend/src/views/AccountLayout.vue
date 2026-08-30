<script setup lang="ts">
import { computed, nextTick, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { i18n } from '../i18n'

const router = useRouter()
const route = useRoute()
const t = i18n.global.t.bind(i18n.global)

const accountNavigationGroups = [
  {
    key: 'accountGroup',
    items: [
      { name: 'account-personal', label: 'personal' },
      { name: 'account-targets', label: 'targets' },
    ],
  },
  {
    key: 'dataGroup',
    items: [
      { name: 'account-imports', label: 'imports' },
      { name: 'account-data-status', label: 'dataStatus' },
      { name: 'account-integrations', label: 'integrations' },
      { name: 'account-data-privacy', label: 'dataPrivacy' },
    ],
  },
  {
    key: 'settingsGroup',
    items: [
      { name: 'account-general', label: 'general' },
      { name: 'account-security', label: 'security' },
    ],
  },
] as const

const accountRouteNames = new Set<string>(
  accountNavigationGroups.flatMap((group) => group.items.map((item) => item.name)),
)
const selectedRouteName = computed(() => (typeof route.name === 'string' ? route.name : ''))
let layoutMounted = false

function isActive(name: string): boolean {
  return selectedRouteName.value === name
}

function selectAccountRoute(event: Event): void {
  const name = (event.target as HTMLSelectElement).value
  if (!accountRouteNames.has(name)) return
  void router.push({ name })
}

async function focusAccountHeading(): Promise<void> {
  if (typeof document === 'undefined') return
  await nextTick()
  const heading = document.querySelector<HTMLElement>('.account-content h1')
  if (!heading) return
  if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1
  heading.focus({ preventScroll: true })
}

onMounted(() => {
  layoutMounted = true
  void focusAccountHeading()
})

watch(
  () => route.fullPath,
  async (_, previousPath) => {
    if (!layoutMounted || previousPath === undefined || !accountRouteNames.has(selectedRouteName.value)) return
    await focusAccountHeading()
  },
  { flush: 'post' },
)
</script>

<template>
  <section class="account-center">
    <aside class="account-navigation">
      <nav :aria-label="t('accountNav.aria')">
        <section v-for="(group, groupIndex) in accountNavigationGroups" :key="group.key" class="account-navigation-group">
          <p :id="`account-navigation-group-${groupIndex}`" class="account-navigation-label">
            {{ t(`accountNav.${group.key}`) }}
          </p>
          <ul class="account-navigation-list" :aria-labelledby="`account-navigation-group-${groupIndex}`">
            <li v-for="item in group.items" :key="item.name">
              <RouterLink
                :to="{ name: item.name }"
                :class="{ active: isActive(item.name) }"
                :aria-current="isActive(item.name) ? 'page' : undefined"
              >
                {{ t(`accountNav.${item.label}`) }}
              </RouterLink>
            </li>
          </ul>
        </section>
      </nav>
    </aside>
    <nav class="account-mobile-navigation" :aria-label="t('accountNav.aria')">
      <label class="field">
        <span>{{ t('accountNav.choose') }}</span>
        <select name="account-section" :value="selectedRouteName" @change="selectAccountRoute">
          <optgroup v-for="group in accountNavigationGroups" :key="group.key" :label="t(`accountNav.${group.key}`)">
            <option v-for="item in group.items" :key="item.name" :value="item.name">
              {{ t(`accountNav.${item.label}`) }}
            </option>
          </optgroup>
        </select>
      </label>
    </nav>
    <div class="account-content">
      <RouterView />
    </div>
  </section>
</template>
