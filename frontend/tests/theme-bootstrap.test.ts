import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const bootstrap = readFileSync(`${process.cwd()}/public/theme-bootstrap.js`, 'utf8')
const metadata = '<meta name="color-scheme" content="light dark"><meta name="theme-color" media="(prefers-color-scheme: light)" content="#f6f7fb"><meta name="theme-color" media="(prefers-color-scheme: dark)" content="#081321">'
const localStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')

function resetDocument() {
  if (localStorageDescriptor) Object.defineProperty(window, 'localStorage', localStorageDescriptor)
  document.head.innerHTML = metadata
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-theme-preference')
  window.localStorage.clear()
  Reflect.deleteProperty(window, 'matchMedia')
}

function resolveTheme(options: { stored?: string; osDark?: boolean; storageDenied?: boolean; mediaUnavailable?: boolean } = {}) {
  resetDocument()
  if (options.storageDenied) {
    Object.defineProperty(window, 'localStorage', { configurable: true, get: () => { throw new Error('denied') } })
  } else if (options.stored !== undefined) {
    window.localStorage.setItem('calograph.theme', options.stored)
  }
  if (!options.mediaUnavailable) {
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: options.osDark ?? false })) })
  }
  window.eval(bootstrap)
  return document
}

beforeEach(resetDocument)
afterEach(resetDocument)

describe('first-paint theme bootstrap', () => {
  it('sets persisted light before application scripts even when OS is dark', () => {
    const doc = resolveTheme({ stored: 'light', osDark: true })
    expect(doc.documentElement.dataset.theme).toBe('light')
    expect(doc.documentElement.dataset.themePreference).toBe('light')
    expect(doc.querySelector('meta[name="color-scheme"]')?.getAttribute('content')).toBe('light')
    expect([...doc.querySelectorAll('meta[name="theme-color"]')].map((meta) => meta.getAttribute('content')))
      .toEqual(['#f6f7fb', '#f6f7fb'])
  })

  it('resolves system from OS light synchronously', () => {
    const doc = resolveTheme({ osDark: false })
    expect(doc.documentElement.dataset.theme).toBe('light')
    expect(doc.documentElement.dataset.themePreference).toBe('system')
  })

  it('normalizes invalid stored values to system', () => {
    const doc = resolveTheme({ stored: 'sunset', osDark: false })
    expect(doc.documentElement.dataset.themePreference).toBe('system')
    expect(doc.documentElement.dataset.theme).toBe('light')
  })

  it('falls back to system if storage is denied', () => {
    const doc = resolveTheme({ storageDenied: true, osDark: false })
    expect(doc.documentElement.dataset.themePreference).toBe('system')
    expect(doc.documentElement.dataset.theme).toBe('light')
  })

  it('uses a deterministic dark fallback when matchMedia is unavailable', () => {
    const doc = resolveTheme({ mediaUnavailable: true })
    expect(doc.documentElement.dataset.themePreference).toBe('system')
    expect(doc.documentElement.dataset.theme).toBe('dark')
  })

  it('applies the root theme synchronously at script evaluation', () => {
    Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false }) })
    expect(document.documentElement.dataset.theme).toBeUndefined()
    window.eval(bootstrap)
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
