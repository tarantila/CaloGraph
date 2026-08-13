<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { formatGermanDateTime } from '../date-format'
import { i18n, intlLocale } from '../i18n'
import type { User } from '../types'
import AdminReauthDialog, { type AdminReauthentication } from './AdminReauthDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const t = i18n.global.t.bind(i18n.global)
function localizeActionError(cause: unknown): string {
  return cause instanceof ApiError
    ? localizeApiError(cause, 'managementUi.actionFailed')
    : t('managementUi.actionFailed')
}

type SimpleAction = 'deactivate' | 'reactivate'
type PrivilegedAction = 'recovery' | 'reset' | 'delete'

interface RecoveryResult {
  token: string
  expiresAt: string
}

const props = defineProps<{
  users: User[]
  currentUserId: string
}>()

const emit = defineEmits<{
  refresh: []
  message: [value: string]
  error: [value: string]
}>()

const simpleAction = ref<SimpleAction | null>(null)
const privilegedAction = ref<PrivilegedAction | null>(null)
const selectedUser = ref<User | null>(null)
const recoveryResult = ref<RecoveryResult | null>(null)
const actionPending = ref(false)
const simpleError = ref('')
const recoveryDialog = ref<HTMLElement | null>(null)
const actionReturnFocus = ref<HTMLElement | null>(null)

const orderedUsers = computed(() => [...props.users].sort((left, right) => {
  if (left.id === props.currentUserId) return -1
  if (right.id === props.currentUserId) return 1
  return left.username.localeCompare(right.username, intlLocale())
}))

const simpleDialog = computed(() => {
  const target = selectedUser.value
  if (!target || !simpleAction.value) return null
  if (simpleAction.value === 'deactivate') {
    return {
      title: t('managementUi.deactivateTitle', { username: target.username }),
      description: t('managementUi.deactivateDescription'),
      confirmLabel: t('managementUi.deactivateConfirm'),
      danger: true,
    }
  }
  return {
    title: t('managementUi.reactivateTitle', { username: target.username }),
    description: t('managementUi.reactivateDescription'),
    confirmLabel: t('managementUi.reactivateConfirm'),
    danger: false,
  }
})

const privilegedDialog = computed(() => {
  const target = selectedUser.value
  if (!target || !privilegedAction.value) return null
  if (privilegedAction.value === 'recovery') {
    return {
      title: t('managementUi.recoveryTitle', { username: target.username }),
      description: t('managementUi.recoveryDescription'),
      submitLabel: t('managementUi.recoveryConfirm'),
      danger: false,
      confirmUsername: undefined,
    }
  }
  if (privilegedAction.value === 'reset') {
    return {
      title: t('managementUi.resetTitle', { username: target.username }),
      description: t('managementUi.resetDescription'),
      submitLabel: t('management.resetAuthenticators'),
      danger: true,
      confirmUsername: undefined,
    }
  }
  return {
    title: t('managementUi.deleteTitle', { username: target.username }),
    description: t('managementUi.deleteDescription'),
    submitLabel: t('managementUi.deleteConfirm'),
    danger: true,
    confirmUsername: target.username,
  }
})

const recoveryLink = computed(() => {
  if (!recoveryResult.value) return ''
  return `${window.location.origin}/recovery#token=${encodeURIComponent(recoveryResult.value.token)}`
})
const modalOpen = computed(
  () => Boolean(simpleDialog.value || privilegedDialog.value || recoveryResult.value),
)


function openSimple(action: SimpleAction, user: User) {
  if (modalOpen.value) return
  selectedUser.value = user
  simpleAction.value = action
  emit('error', '')
}

function closeSimple() {
  if (actionPending.value) return
  simpleError.value = ''
  simpleAction.value = null
  selectedUser.value = null
}

function openPrivileged(action: PrivilegedAction, user: User) {
  if (modalOpen.value) return
  actionReturnFocus.value =
    document.activeElement instanceof HTMLElement ? document.activeElement : null
  selectedUser.value = user
  privilegedAction.value = action
  emit('error', '')
}

function closePrivileged() {
  privilegedAction.value = null
  selectedUser.value = null
}

async function runSimpleAction() {
  const action = simpleAction.value
  const target = selectedUser.value
  if (!action || !target || actionPending.value) return
  actionPending.value = true
  simpleError.value = ''
  emit('error', '')
  try {
    await api(`/users/${target.id}/${action}`, { method: 'POST' })
    emit('message', action === 'deactivate'
      ? t('managementUi.deactivated', { username: target.username })
      : t('managementUi.reactivated', { username: target.username }))
    emit('refresh')
    simpleAction.value = null
    selectedUser.value = null
  } catch (cause) {
    simpleError.value = localizeActionError(cause)
    emit('error', simpleError.value)
  } finally {
    actionPending.value = false
  }
}

async function runPrivilegedAction(credentials: AdminReauthentication) {
  const action = privilegedAction.value
  const target = selectedUser.value
  if (!action || !target) return
  if (action === 'recovery') {
    const result = await api<{ recovery_token: string; expires_at: string }>(
      `/users/${target.id}/recovery-links`,
      { method: 'POST', body: JSON.stringify(credentials) },
    )
    recoveryResult.value = { token: result.recovery_token, expiresAt: result.expires_at }
    emit('message', t('managementUi.recoveryIssued', { username: target.username }))
  } else if (action === 'reset') {
    await api(`/users/${target.id}/authenticators/reset`, {
      method: 'POST',
      body: JSON.stringify(credentials),
    })
    emit('message', t('managementUi.authenticatorsReset', { username: target.username }))
  } else {
    await api(`/users/${target.id}`, {
      method: 'DELETE',
      body: JSON.stringify(credentials),
    })
    emit('message', t('managementUi.deleted', { username: target.username }))
  }
  emit('refresh')
}

async function copyRecoveryLink() {
  if (!recoveryResult.value) return
  try {
    await navigator.clipboard.writeText(recoveryLink.value)
    emit('message', t('managementUi.recoveryCopied'))
  } catch {
    emit('error', t('managementUi.recoveryCopyFailed'))
  }
}

function closeRecoveryResult() {
  recoveryResult.value = null
  actionReturnFocus.value?.focus()
  actionReturnFocus.value = null
}

watch(recoveryResult, async (result) => {
  if (!result) return
  await nextTick()
  recoveryDialog.value?.focus()
})

function formatTimestamp(value: string) {
  return formatGermanDateTime(value)
}
</script>

<template>
  <div class="user-management">
    <div
      class="table-scroll"
      :inert="modalOpen"
      :aria-hidden="modalOpen ? 'true' : undefined"
    >
      <table>
        <thead>
          <tr><th>{{ t('management.users') }}</th><th>{{ t('management.role') }}</th><th>{{ t('common.status') }}</th><th>{{ t('common.actions') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in orderedUsers" :key="item.id">
            <td>
              <strong>{{ item.username }}</strong>
              <span v-if="item.id === props.currentUserId" class="current-user-label">{{ t('management.current') }}</span>
            </td>
            <td>{{ item.is_admin ? t('management.administrator') : t('management.user') }}</td>
            <td>
              <span class="status-badge" :class="item.is_active ? 'success' : 'inactive'">
                {{ item.is_active ? t('management.active') : t('management.inactive') }}
              </span>
              <small v-if="!item.is_active && item.deactivated_at" class="status-detail">
                {{ t('managementUi.since', { date: formatTimestamp(item.deactivated_at) }) }}
              </small>
            </td>
            <td>
              <span v-if="item.id === props.currentUserId" class="muted">{{ t('management.ownAccount') }}</span>
              <div v-else class="user-actions">
                <button
                  v-if="item.is_active"
                  class="text-button danger-text"
                  type="button"
                  @click="openSimple('deactivate', item)"
                >{{ t('management.deactivate') }}</button>
                <template v-else>
                  <button class="text-button" type="button" @click="openSimple('reactivate', item)">{{ t('management.reactivate') }}</button>
                  <button class="text-button" type="button" @click="openPrivileged('recovery', item)">{{ t('management.issueRecovery') }}</button>
                  <button class="text-button" type="button" @click="openPrivileged('reset', item)">{{ t('management.resetAuthenticators') }}</button>
                  <button class="text-button danger-text" type="button" @click="openPrivileged('delete', item)">{{ t('management.delete') }}</button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <ConfirmDialog
      v-if="simpleDialog"
      :open="true"
      :title="simpleDialog.title"
      :description="simpleDialog.description"
      :confirm-label="actionPending ? t('managementUi.running') : simpleDialog.confirmLabel"
      :danger="simpleDialog.danger"
      :pending="actionPending"
      :error="simpleError"
      @close="closeSimple"
      @confirm="runSimpleAction"
    />

    <AdminReauthDialog
      v-if="privilegedDialog"
      :open="true"
      :title="privilegedDialog.title"
      :description="privilegedDialog.description"
      :submit-label="privilegedDialog.submitLabel"
      :danger="privilegedDialog.danger"
      :confirm-username="privilegedDialog.confirmUsername"
      :perform="runPrivilegedAction"
      @close="closePrivileged"
    />

    <div
      v-if="recoveryResult"
      class="dialog-backdrop"
      role="presentation"
      @click.self="closeRecoveryResult"
      @keydown.esc="closeRecoveryResult"
    >
      <section
        ref="recoveryDialog"
        class="card action-dialog recovery-result"
        role="dialog"
        aria-modal="true"
        aria-labelledby="recovery-result-title"
        tabindex="-1"
      >
        <h2 id="recovery-result-title">{{ t('managementUi.recoveryHeading') }}</h2>
        <p>{{ t('managementUi.recoveryDescriptionResult', { date: formatTimestamp(recoveryResult.expiresAt) }) }}</p>
        <code>{{ recoveryLink }}</code>
        <div class="dialog-actions">
          <button class="button secondary" type="button" @click="closeRecoveryResult">{{ t('common.close') }}</button>
          <button class="button" type="button" @click="copyRecoveryLink">{{ t('management.copyLink') }}</button>
        </div>
      </section>
    </div>
  </div>
</template>
