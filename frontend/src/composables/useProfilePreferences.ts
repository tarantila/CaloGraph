import { reactive, ref } from 'vue'

import { api } from '../api'
import type { User } from '../types'
import { useAuthStore } from '../stores/auth'

export type SupportedLanguage = 'de' | 'en'
export type PreferredWeightUnit = 'kg' | 'lb'

export interface ProfilePreferences extends User {
  language: SupportedLanguage
  preferred_weight_unit: PreferredWeightUnit
}

export interface ProfilePreferencesPayload {
  language: SupportedLanguage
  timezone: string
  week_starts_on: number
  preferred_weight_unit: PreferredWeightUnit
  raw_payload_retention_days: number
}

const emptyProfile: ProfilePreferences = {
  id: '',
  username: '',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  preferred_weight_unit: 'kg',
  raw_payload_retention_days: 0,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

function normalizeProfile(value: ProfilePreferences): ProfilePreferences {
  return {
    ...value,
    language: value.language === 'en' ? 'en' : 'de',
    preferred_weight_unit: value.preferred_weight_unit === 'lb' ? 'lb' : 'kg',
  }
}

export function useProfilePreferences() {
  const auth = useAuthStore()
  const profile = reactive<ProfilePreferences>({ ...emptyProfile })
  const loaded = ref(false)
  let loadGeneration = 0

  function assign(value: ProfilePreferences): void {
    Object.assign(profile, normalizeProfile(value))
  }

  async function load(): Promise<boolean> {
    const generation = ++loadGeneration
    const result = await auth.enqueueProfileRead(() =>
      api<ProfilePreferences>('/settings/profile'),
    )
    if (generation !== loadGeneration || !auth.isCurrentProfileUpdate(result.generation)) return false

    const currentLanguage: SupportedLanguage = auth.user?.id === result.value.id
      ? auth.user.language === 'en' ? 'en' : 'de'
      : result.value.language === 'en' ? 'en' : 'de'
    const loadedProfile = normalizeProfile({ ...result.value, language: currentLanguage })
    assign(loadedProfile)
    auth.syncLoadedUser(result.generation, loadedProfile)
    loaded.value = true
    return true
  }

  async function save(): Promise<ProfilePreferences | null> {
    const generation = auth.beginProfileUpdate()
    const payload: ProfilePreferencesPayload = {
      language: profile.language,
      timezone: profile.timezone,
      week_starts_on: profile.week_starts_on,
      preferred_weight_unit: profile.preferred_weight_unit,
      raw_payload_retention_days: profile.raw_payload_retention_days,
    }
    let committed = false
    const result = await auth.enqueueProfileUpdate(
      generation,
      () => api<ProfilePreferences>('/settings/profile', {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
      (value) => {
        committed = auth.commitProfileUpdate(generation, value)
      },
    )
    if (!result || !committed) return null
    const savedProfile = normalizeProfile(result)
    assign(savedProfile)
    loaded.value = true
    return savedProfile
  }

  function invalidate(): void {
    loadGeneration += 1
  }

  return { profile, loaded, load, save, invalidate }
}
