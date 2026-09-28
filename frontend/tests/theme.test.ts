import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Listener = (event: MediaQueryListEvent) => void

const localStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')

function installMedia(matches: boolean) {
  const listeners = new Set<Listener>()
  const media = {
    matches,
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: vi.fn((_type: string, listener: Listener) => listeners.add(listener)),
    removeEventListener: vi.fn((_type: string, listener: Listener) => listeners.delete(listener)),
    dispatch(next: boolean) {
      this.matches = next
      for (const listener of listeners) listener({ matches: next } as MediaQueryListEvent)
    },
    get listenerCount() { return listeners.size },
  }
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => media) })
  return media
}

function addThemeMetadata() {
  document.head.innerHTML = '<meta name="color-scheme" content="light dark"><meta name="theme-color" media="(prefers-color-scheme: light)" content="#f6f7fb"><meta name="theme-color" media="(prefers-color-scheme: dark)" content="#081321">'
}

describe('theme preference runtime', () => {
  // vi.resetModules simulates a new document runtime after changing persisted storage.
  // A static import would retain the same module singleton across that reload simulation.
  beforeEach(() => {
    vi.resetModules()
    window.localStorage.clear()
    addThemeMetadata()
    delete document.documentElement.dataset.theme
    delete document.documentElement.dataset.themePreference
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: undefined })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    if (localStorageDescriptor) Object.defineProperty(window, 'localStorage', localStorageDescriptor)
    Reflect.deleteProperty(window, 'matchMedia')
  })

  it.each([[true, 'dark'], [false, 'light']] as const)('resolves default system preference for OS dark=%s', async (matches, expected) => {
    const media = installMedia(matches)
    const theme = await import('../src/theme')
    theme.initializeTheme()
    expect(theme.themePreference.value).toBe('system')
    expect(theme.effectiveTheme.value).toBe(expected)
    expect(document.documentElement.dataset.theme).toBe(expected)
    expect(document.documentElement.dataset.themePreference).toBe('system')
    expect(document.querySelector('meta[name="color-scheme"]')?.getAttribute('content')).toBe(expected)
    expect([...document.querySelectorAll('meta[name="theme-color"]')].map((meta) => meta.getAttribute('content')))
      .toEqual([expected === 'light' ? '#f6f7fb' : '#081321', expected === 'dark' ? '#081321' : '#f6f7fb'])
    expect(media.listenerCount).toBe(1)
  })

  it.each(['light', 'dark'] as const)('uses explicit %s and ignores later OS changes', async (choice) => {
    const media = installMedia(choice === 'dark')
    window.localStorage.setItem('calograph.theme', choice)
    const theme = await import('../src/theme')
    theme.initializeTheme()
    media.dispatch(choice !== 'dark')
    expect(theme.themePreference.value).toBe(choice)
    expect(theme.effectiveTheme.value).toBe(choice)
    expect(media.listenerCount).toBe(0)
  })

  it('persists a selected preference and reload initialization restores it', async () => {
    installMedia(false)
    const theme = await import('../src/theme')
    theme.initializeTheme()
    theme.setThemePreference('dark')
    expect(window.localStorage.getItem('calograph.theme')).toBe('dark')
    vi.resetModules()
    const reloaded = await import('../src/theme')
    reloaded.initializeTheme()
    expect(reloaded.themePreference.value).toBe('dark')
    expect(reloaded.effectiveTheme.value).toBe('dark')
  })

  it('falls back from invalid persisted preference to live system resolution', async () => {
    window.localStorage.setItem('calograph.theme', 'sunset')
    installMedia(false)
    const theme = await import('../src/theme')
    theme.initializeTheme()
    expect(theme.themePreference.value).toBe('system')
    expect(theme.effectiveTheme.value).toBe('light')
  })

  it('uses deterministic dark system fallback if matchMedia is unavailable or throws', async () => {
    const theme = await import('../src/theme')
    theme.initializeTheme()
    expect(theme.themePreference.value).toBe('system')
    expect(theme.effectiveTheme.value).toBe('dark')
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => { throw new Error('unavailable') } })
    expect(() => theme.initializeTheme()).not.toThrow()
    expect(theme.effectiveTheme.value).toBe('dark')
  })

  it('survives an unavailable localStorage getter and applies in-memory choices', async () => {
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new Error('denied') } })
    installMedia(false)
    const theme = await import('../src/theme')
    expect(() => theme.initializeTheme()).not.toThrow()
    expect(theme.effectiveTheme.value).toBe('light')
    expect(() => theme.setThemePreference('dark')).not.toThrow()
    expect(theme.effectiveTheme.value).toBe('dark')
  })

  it('survives getItem and setItem failures without claiming persisted state', async () => {
    const storage = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => { throw new Error('denied') },
        setItem: () => { throw new Error('denied') },
      } as unknown as Storage,
    })
    installMedia(false)
    const theme = await import('../src/theme')
    expect(() => theme.initializeTheme()).not.toThrow()
    expect(theme.themePreference.value).toBe('system')
    expect(() => theme.setThemePreference('light')).not.toThrow()
    expect(theme.effectiveTheme.value).toBe('light')
    Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
    vi.resetModules()
    const reloaded = await import('../src/theme')
    reloaded.initializeTheme()
    expect(reloaded.themePreference.value).toBe('system')
  })

  it('replaces OS listeners idempotently and only follows the OS in system mode', async () => {
    const media = installMedia(false)
    const theme = await import('../src/theme')
    theme.initializeTheme()
    theme.initializeTheme()
    expect(media.listenerCount).toBe(1)
    theme.setThemePreference('dark')
    expect(media.listenerCount).toBe(0)
    media.dispatch(false)
    expect(theme.effectiveTheme.value).toBe('dark')
    theme.setThemePreference('system')
    expect(media.listenerCount).toBe(1)
    media.dispatch(true)
    expect(theme.effectiveTheme.value).toBe('dark')
    media.dispatch(false)
    expect(theme.effectiveTheme.value).toBe('light')
  })
})
