import { ref, watch } from 'vue'

import { canonicalAnalysisCardId, DEFAULT_ACTIVE_CARD_IDS } from '../analysis-v2'
import { api, localizeApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import type { AnalysisPreferencesResponse, AnalysisPreferencesUpdate } from '../types'

const sharedState = {
  userId: null as string | null,
  revision: 0,
  activeCardIds: ref<string[]>([...DEFAULT_ACTIVE_CARD_IDS]),
  saving: ref(false),
  saveError: ref(''),
}

function stateForCurrentUser() {
  const userId = useAuthStore().user?.id ?? null
  if (sharedState.userId !== userId) {
    sharedState.userId = userId
    sharedState.revision += 1
    sharedState.activeCardIds.value = [...DEFAULT_ACTIVE_CARD_IDS]
    sharedState.saving.value = false
    sharedState.saveError.value = ''
  }
  return sharedState
}


function canonicalCardIds(ids: readonly string[]): string[] {
  const seen = new Set<string>()
  return ids.flatMap((id) => {
    const canonicalId = canonicalAnalysisCardId(id)
    if (seen.has(canonicalId)) return []
    seen.add(canonicalId)
    return [canonicalId]
  })
}
function currentRequest(revision: number, userId: string | null): boolean {
  return revision === sharedState.revision
    && sharedState.userId === userId
    && (useAuthStore().user?.id ?? null) === userId
}

export function useAnalysisPreferences() {
  const auth = useAuthStore()
  const state = stateForCurrentUser()
  watch(
    () => auth.user?.id ?? null,
    () => { stateForCurrentUser() },
    { flush: 'sync' },
  )
  async function load(): Promise<void> {
    const current = stateForCurrentUser()
    if (current.saving.value) return
    const userId = current.userId
    const revision = ++current.revision
    try {
      const response = await api<AnalysisPreferencesResponse>('/settings/analysis-preferences')
      if (!currentRequest(revision, userId) || current.saving.value) return
      current.activeCardIds.value = canonicalCardIds(response.active_card_ids)
      current.saveError.value = ''
    } catch (cause) {
      if (currentRequest(revision, userId)) throw cause
    }
  }

  async function replaceActiveCardIds(nextIds: string[]): Promise<boolean> {
    const current = stateForCurrentUser()
    if (current.saving.value) return false

    const update: AnalysisPreferencesUpdate = { active_card_ids: canonicalCardIds(nextIds) }
    const previousIds = [...current.activeCardIds.value]
    current.activeCardIds.value = [...update.active_card_ids]
    current.saveError.value = ''
    current.saving.value = true
    const userId = current.userId
    const revision = ++current.revision
    try {
      const response = await api<AnalysisPreferencesResponse>(
        '/settings/analysis-preferences',
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(update),
        },
      )
      if (!currentRequest(revision, userId)) return false
      current.activeCardIds.value = canonicalCardIds(response.active_card_ids)
      return true
    } catch (cause) {
      if (!currentRequest(revision, userId)) return false
      current.activeCardIds.value = previousIds
      current.saveError.value = localizeApiError(cause, 'analysisV2.saveFailed')
      return false
    } finally {
      if (currentRequest(revision, userId)) current.saving.value = false
    }
  }

  return {
    activeCardIds: state.activeCardIds,
    saving: state.saving,
    saveError: state.saveError,
    load,
    replaceActiveCardIds,
  }
}
