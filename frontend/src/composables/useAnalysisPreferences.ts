import { ref } from 'vue'

import { api, localizeApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import type { AnalysisPreferencesResponse } from '../types'

const sharedState = {
  userId: null as string | null,
  revision: 0,
  selectedKeys: ref<string[]>([]),
  saving: ref(false),
  saveError: ref(''),
}

function stateForCurrentUser() {
  const userId = useAuthStore().user?.id ?? null
  if (sharedState.userId !== userId) {
    sharedState.userId = userId
    sharedState.revision += 1
    sharedState.selectedKeys.value = []
    sharedState.saving.value = false
    sharedState.saveError.value = ''
  }
  return sharedState
}

export function useAnalysisPreferences() {
  const state = stateForCurrentUser()

  async function load(): Promise<void> {
    if (state.saving.value) return
    const revision = state.revision
    const response = await api<AnalysisPreferencesResponse>('/settings/analysis-preferences')
    if (revision !== state.revision || state.saving.value) return
    state.selectedKeys.value = [...response.analysis_keys]
    state.saveError.value = ''
  }

  async function replace(nextKeys: string[]): Promise<boolean> {
    if (state.saving.value) return false
    const previousKeys = [...state.selectedKeys.value]
    state.selectedKeys.value = [...nextKeys]
    state.saveError.value = ''
    state.saving.value = true
    const revision = ++state.revision
    try {
      const response = await api<AnalysisPreferencesResponse>('/settings/analysis-preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ analysis_keys: nextKeys }),
      })
      if (revision !== state.revision) return false
      state.selectedKeys.value = [...response.analysis_keys]
      return true
    } catch (cause) {
      if (revision !== state.revision) return false
      state.selectedKeys.value = previousKeys
      state.saveError.value = localizeApiError(cause, 'analysisV2.saveFailed')
      return false
    } finally {
      if (revision === state.revision) state.saving.value = false
    }
  }

  return {
    selectedKeys: state.selectedKeys,
    saving: state.saving,
    saveError: state.saveError,
    load,
    replace,
  }
}
