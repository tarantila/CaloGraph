import { shallowRef } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type EffectiveTheme = 'light' | 'dark'

const storageKey = 'calograph.theme'
const darkColor = '#081321'
const lightColor = '#f6f7fb'
const systemQuery = '(prefers-color-scheme: dark)'

export const themePreference = shallowRef<ThemePreference>('system')
export const effectiveTheme = shallowRef<EffectiveTheme>('dark')

let systemMedia: MediaQueryList | null = null

function readPreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(storageKey)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

function detachSystemListener(): void {
  systemMedia?.removeEventListener('change', handleSystemChange)
  systemMedia = null
}

function attachSystemListener(): void {
  if (themePreference.value !== 'system') return
  try {
    const media = window.matchMedia(systemQuery)
    media.addEventListener('change', handleSystemChange)
    systemMedia = media
  } catch {
    systemMedia = null
  }
}

function resolveEffectiveTheme(): EffectiveTheme {
  if (themePreference.value !== 'system') return themePreference.value
  return systemMedia?.matches ? 'dark' : systemMedia ? 'light' : 'dark'
}

function applyTheme(): void {
  const preference = themePreference.value
  const effective = resolveEffectiveTheme()
  const root = document.documentElement
  root.dataset.themePreference = preference
  root.dataset.theme = effective
  document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')?.setAttribute('content', effective)
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((meta) => {
    meta.content = effective === 'dark' ? darkColor : lightColor
  })
  effectiveTheme.value = effective
}

function handleSystemChange(): void {
  if (themePreference.value === 'system') applyTheme()
}

export function initializeTheme(): void {
  detachSystemListener()
  themePreference.value = readPreference()
  attachSystemListener()
  applyTheme()
}

export function setThemePreference(next: ThemePreference): void {
  if (next !== 'system' && next !== 'light' && next !== 'dark') return
  detachSystemListener()
  themePreference.value = next
  attachSystemListener()
  applyTheme()
  try {
    window.localStorage.setItem(storageKey, next)
  } catch {
    // Keep this session's selected appearance even when persistence is denied.
  }
}
