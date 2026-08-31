<script setup lang="ts">
import {
  PhDatabase,
  PhGear,
  PhLockKey,
  PhPlugs,
  PhShieldCheck,
  PhTarget,
  PhUploadSimple,
  PhUserCircle,
} from '@phosphor-icons/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
const router = useRouter()
const route = useRoute()
const accountContent = ref<HTMLElement | null>(null)

const accountNavigationGroups = [
  {
    key: 'accountGroup',
    items: [
      { name: 'account-personal', label: 'personal', icon: PhUserCircle },
      { name: 'account-targets', label: 'targets', icon: PhTarget },
    ],
  },
  {
    key: 'dataGroup',
    items: [
      { name: 'account-imports', label: 'imports', icon: PhUploadSimple },
      { name: 'account-data-status', label: 'dataStatus', icon: PhDatabase },
      { name: 'account-integrations', label: 'integrations', icon: PhPlugs },
      { name: 'account-data-privacy', label: 'dataPrivacy', icon: PhShieldCheck },
    ],
  },
  {
    key: 'settingsGroup',
    items: [
      { name: 'account-general', label: 'general', icon: PhGear },
      { name: 'account-security', label: 'security', icon: PhLockKey },
    ],
  },
] as const

const accountRouteNames = new Set<string>(
  accountNavigationGroups.flatMap((group) => group.items.map((item) => item.name)),
)
const selectedRouteName = computed(() => (typeof route.name === 'string' ? route.name : ''))
const accountContentWidthClasses: Record<string, string> = {
  'account-personal': 'account-content--comfortable',
  'account-targets': 'account-content--wide',
  'account-imports': 'account-content--wide',
  'account-data-status': 'account-content--wide',
  'account-integrations': 'account-content--comfortable',
  'account-data-privacy': 'account-content--comfortable',
  'account-general': 'account-content--comfortable',
  'account-security': 'account-content--compact',
}
const accountContentClass = computed(() => (
  accountContentWidthClasses[selectedRouteName.value] ?? 'account-content--comfortable'
))

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
  await nextTick()
  const heading = accountContent.value?.querySelector<HTMLElement>('h1')
  if (!heading) return
  if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1
  heading.classList.add('account-heading-focus')
  heading.focus()
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
  <section class="account-center" aria-labelledby="account-center-title">
    <aside class="account-navigation admin-sidebar" :aria-label="t('accountNav.title')">
      <h1 id="account-center-title">{{ t('accountNav.title') }}</h1>
      <nav class="admin-sidebar-nav account-navigation-nav" :aria-label="t('accountNav.aria')" tabindex="0">
        <section v-for="(group, groupIndex) in accountNavigationGroups" :key="group.key" class="account-navigation-group">
          <p :id="`account-navigation-group-${groupIndex}`" class="admin-sidebar-label account-navigation-label">
            {{ t(`accountNav.${group.key}`) }}
          </p>
          <ul class="account-navigation-list" :aria-labelledby="`account-navigation-group-${groupIndex}`">
            <li v-for="item in group.items" :key="item.name">
              <RouterLink
                :to="{ name: item.name }"
                :class="{ active: isActive(item.name) }"
                :aria-current="isActive(item.name) ? 'page' : undefined"
              >
                <component :is="item.icon" :size="18" aria-hidden="true" />
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
    <div ref="accountContent" :class="['account-content', accountContentClass]">
      <RouterView />
    </div>
  </section>
</template>
