import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Die Analyse konnte nicht gespeichert werden.'),
}))

import { DEFAULT_ACTIVE_CARD_IDS } from '../src/analysis-v2'
import { useAnalysisPreferences } from '../src/composables/useAnalysisPreferences'
import { useAuthStore } from '../src/stores/auth'
import type { AnalysisPreferencesResponse } from '../src/types'

let accountNumber = 0
const defaultCards = [...DEFAULT_ACTIVE_CARD_IDS]

function response(activeCardIds: string[]): AnalysisPreferencesResponse {
  return { active_card_ids: [...activeCardIds] }
}

function setUser(id: string): void {
  useAuthStore().user = {
    id,
    username: 'owner',
    language: 'de',
    timezone: 'Europe/Berlin',
    week_starts_on: 0,
    raw_payload_retention_days: 0,
    is_admin: false,
    is_active: true,
    deactivated_at: null,
  }
}

function deferredResponse() {
  let resolve!: (value: AnalysisPreferencesResponse) => void
  const promise = new Promise<AnalysisPreferencesResponse>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  apiMock.mockReset()
  setActivePinia(createPinia())
  setUser(`preference-user-${++accountNumber}`)
})

it('defaults a new account to the four removable domain cards', () => {
  const preferences = useAnalysisPreferences()
  expect(preferences.activeCardIds.value).toEqual(defaultCards)
})

it('loads and replaces one mixed card sequence without splitting card kinds', async () => {
  const preferences = useAnalysisPreferences()
  const initial = ['weight', 'vitamin_a_ug', 'nutrition', 'meals_today', 'activity']
  apiMock.mockResolvedValueOnce(response(initial))
  await preferences.load()
  expect(preferences.activeCardIds.value).toEqual(initial)

  const next = ['activity', 'vitamin_a_ug', 'weight', 'meals_today', 'nutrition']
  apiMock.mockResolvedValueOnce(response(next))
  expect(await preferences.replaceActiveCardIds(next)).toBe(true)
  expect(JSON.parse(String(apiMock.mock.calls[1]![1].body))).toEqual({ active_card_ids: next })
  expect(preferences.activeCardIds.value).toEqual(next)
})

it('applies a cross-type reorder optimistically while its save is pending', async () => {
  const preferences = useAnalysisPreferences()
  const next = ['weight', 'protein_g', 'nutrition', 'vitamin_a_ug', 'activity']
  const pending = deferredResponse()
  apiMock.mockReturnValueOnce(pending.promise)

  const save = preferences.replaceActiveCardIds(next)
  expect(preferences.activeCardIds.value).toEqual(next)
  expect(preferences.saving.value).toBe(true)

  pending.resolve(response(next))
  expect(await save).toBe(true)
  expect(preferences.activeCardIds.value).toEqual(next)
  expect(preferences.saving.value).toBe(false)
})

it('restores the entire previous sequence and localizes a rejected save', async () => {
  const preferences = useAnalysisPreferences()
  const original = ['weight', 'vitamin_a_ug', 'nutrition', 'activity']
  apiMock.mockResolvedValueOnce(response(original))
  await preferences.load()

  apiMock.mockRejectedValueOnce(new Error('synthetic failure'))
  expect(await preferences.replaceActiveCardIds(['activity', 'meals_today'])).toBe(false)
  expect(preferences.activeCardIds.value).toEqual(original)
  expect(preferences.saveError.value).toBe('Die Analyse konnte nicht gespeichert werden.')
  expect(preferences.saving.value).toBe(false)
})

it('rejects a second preference write while the first request is pending', async () => {
  const preferences = useAnalysisPreferences()
  const pending = deferredResponse()
  apiMock.mockReturnValueOnce(pending.promise)

  const first = ['weight', 'nutrition', 'activity', 'micronutrients']
  const firstSave = preferences.replaceActiveCardIds(first)
  expect(await preferences.replaceActiveCardIds(['nutrition', 'weight'])).toBe(false)
  expect(apiMock).toHaveBeenCalledTimes(1)
  pending.resolve(response(first))
  expect(await firstSave).toBe(true)
})

it('does not let a delayed GET overwrite a newer canonical PUT', async () => {
  const preferences = useAnalysisPreferences()
  const staleGet = deferredResponse()
  apiMock.mockReturnValueOnce(staleGet.promise)
  const load = preferences.load()

  const current = ['weight', 'protein_g', 'nutrition', 'activity']
  apiMock.mockResolvedValueOnce(response(current))
  expect(await preferences.replaceActiveCardIds(current)).toBe(true)
  staleGet.resolve(response(defaultCards))
  await load

  expect(preferences.activeCardIds.value).toEqual(current)
})

it('discards a delayed GET for account A after account B becomes current', async () => {
  const accountA = useAnalysisPreferences()
  const staleGet = deferredResponse()
  apiMock.mockReturnValueOnce(staleGet.promise)
  const load = accountA.load()

  setUser(`preference-user-${++accountNumber}`)
  const accountB = useAnalysisPreferences()
  expect(accountB.activeCardIds.value).toEqual(defaultCards)
  staleGet.resolve(response(['activity', 'meals_today']))
  await load

  expect(accountB.activeCardIds.value).toEqual(defaultCards)
})

it('discards a delayed PUT for account A after account B becomes current', async () => {
  const accountA = useAnalysisPreferences()
  const stalePut = deferredResponse()
  apiMock.mockReturnValueOnce(stalePut.promise)
  const save = accountA.replaceActiveCardIds(['activity', 'meals_today'])

  setUser(`preference-user-${++accountNumber}`)
  const accountB = useAnalysisPreferences()
  stalePut.resolve(response(['activity', 'meals_today']))
  expect(await save).toBe(false)
  expect(accountB.activeCardIds.value).toEqual(defaultCards)
  expect(accountB.saving.value).toBe(false)
})

it('resets the canonical list and save state when the user logs out', async () => {
  const preferences = useAnalysisPreferences()
  apiMock.mockResolvedValueOnce(response(['activity', 'meals_today']))
  await preferences.load()

  useAuthStore().user = null
  const loggedOut = useAnalysisPreferences()
  expect(loggedOut.activeCardIds.value).toEqual(defaultCards)
  expect(loggedOut.saveError.value).toBe('')
  expect(loggedOut.saving.value).toBe(false)
})

it('admits only the latest of two overlapping preference loads', async () => {
  const preferences = useAnalysisPreferences()
  const older = deferredResponse()
  const newer = deferredResponse()
  apiMock.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)

  const olderLoad = preferences.load()
  const newerLoad = preferences.load()
  const current = ['nutrition', 'meals_today', 'activity']
  newer.resolve(response(current))
  await newerLoad
  older.resolve(response(defaultCards))
  await olderLoad

  expect(preferences.activeCardIds.value).toEqual(current)
})
