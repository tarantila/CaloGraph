<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { api, ApiError } from '../api'
import type { User } from '../types'
import AdminReauthDialog, { type AdminReauthentication } from './AdminReauthDialog.vue'
import ConfirmDialog from './ConfirmDialog.vue'

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
  return left.username.localeCompare(right.username, 'de')
}))

const simpleDialog = computed(() => {
  const target = selectedUser.value
  if (!target || !simpleAction.value) return null
  if (simpleAction.value === 'deactivate') {
    return {
      title: `${target.username} deaktivieren?`,
      description: 'Der Benutzer kann sich danach nicht mehr anmelden. Sitzungen und API-Tokens werden ungültig, offene Einladungen widerrufen und die YAZIO-Synchronisierung pausiert. Ernährungs- und Importdaten bleiben erhalten.',
      confirmLabel: 'Benutzer deaktivieren',
      danger: true,
    }
  }
  return {
    title: `${target.username} reaktivieren?`,
    description: 'Der Benutzer kann sich danach wieder mit seinem aktuellen Passwort anmelden. Alte Sitzungen, API-Tokens und Einladungen bleiben ungültig; die YAZIO-Synchronisierung bleibt pausiert.',
    confirmLabel: 'Benutzer reaktivieren',
    danger: false,
  }
})

const privilegedDialog = computed(() => {
  const target = selectedUser.value
  if (!target || !privilegedAction.value) return null
  if (privilegedAction.value === 'recovery') {
    return {
      title: `Recovery für ${target.username} ausstellen`,
      description: 'Das Konto wird deaktiviert. Sitzungen und API-Tokens werden ungültig und die YAZIO-Synchronisierung wird pausiert. Der einmal sichtbare Recovery-Link ist 30 Minuten gültig und reaktiviert das Konto nicht.',
      submitLabel: 'Recovery-Link ausstellen',
      danger: false,
      confirmUsername: undefined,
    }
  }
  if (privilegedAction.value === 'reset') {
    return {
      title: `Authentikatoren von ${target.username} zurücksetzen`,
      description: 'TOTP, Recovery-Codes, Passkeys und WebAuthn-Anmeldedaten werden entfernt. Sitzungen und API-Tokens werden ungültig. Passwort, Profil und Ernährungsdaten bleiben erhalten.',
      submitLabel: 'Authentikatoren zurücksetzen',
      danger: true,
      confirmUsername: undefined,
    }
  }
  return {
    title: `${target.username} endgültig löschen`,
    description: 'Diese Aktion ist endgültig und löscht das deaktivierte Konto einschließlich seiner personenbezogenen Daten, Importe, Ziele und YAZIO-Verbindung. Sie kann nicht rückgängig gemacht werden.',
    submitLabel: 'Konto endgültig löschen',
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
      ? `${target.username} wurde deaktiviert.`
      : `${target.username} wurde reaktiviert.`)
    emit('refresh')
    simpleAction.value = null
    selectedUser.value = null
  } catch (cause) {
    simpleError.value =
      cause instanceof ApiError ? cause.message : 'Die Benutzeraktion konnte nicht ausgeführt werden.'
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
    emit('message', `Recovery für ${target.username} wurde ausgestellt; das Konto ist deaktiviert.`)
  } else if (action === 'reset') {
    await api(`/users/${target.id}/authenticators/reset`, {
      method: 'POST',
      body: JSON.stringify(credentials),
    })
    emit('message', `Authentikatoren von ${target.username} wurden zurückgesetzt.`)
  } else {
    await api(`/users/${target.id}`, {
      method: 'DELETE',
      body: JSON.stringify(credentials),
    })
    emit('message', `${target.username} wurde endgültig gelöscht.`)
  }
  emit('refresh')
}

async function copyRecoveryLink() {
  if (!recoveryResult.value) return
  try {
    await navigator.clipboard.writeText(recoveryLink.value)
    emit('message', 'Recovery-Link wurde in die Zwischenablage kopiert.')
  } catch {
    emit('error', 'Recovery-Link konnte nicht kopiert werden.')
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
  return new Date(value).toLocaleString('de-DE')
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
          <tr><th>Benutzer</th><th>Rolle</th><th>Status</th><th>Aktionen</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in orderedUsers" :key="item.id">
            <td>
              <strong>{{ item.username }}</strong>
              <span v-if="item.id === props.currentUserId" class="current-user-label">Du</span>
            </td>
            <td>{{ item.is_admin ? 'Administrator' : 'Benutzer' }}</td>
            <td>
              <span class="status-badge" :class="item.is_active ? 'success' : 'inactive'">
                {{ item.is_active ? 'Aktiv' : 'Deaktiviert' }}
              </span>
              <small v-if="!item.is_active && item.deactivated_at" class="status-detail">
                seit {{ formatTimestamp(item.deactivated_at) }}
              </small>
            </td>
            <td>
              <span v-if="item.id === props.currentUserId" class="muted">Eigenes Konto</span>
              <div v-else class="user-actions">
                <button
                  v-if="item.is_active"
                  class="text-button danger-text"
                  type="button"
                  @click="openSimple('deactivate', item)"
                >Deaktivieren</button>
                <template v-else>
                  <button class="text-button" type="button" @click="openSimple('reactivate', item)">Reaktivieren</button>
                  <button class="text-button" type="button" @click="openPrivileged('recovery', item)">Recovery ausstellen</button>
                  <button class="text-button" type="button" @click="openPrivileged('reset', item)">Authentikatoren zurücksetzen</button>
                  <button class="text-button danger-text" type="button" @click="openPrivileged('delete', item)">Endgültig löschen</button>
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
      :confirm-label="actionPending ? 'Wird ausgeführt …' : simpleDialog.confirmLabel"
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
        <h2 id="recovery-result-title">Recovery-Link jetzt sicher weitergeben</h2>
        <p>Dieser Link wird nur einmal angezeigt und ist bis {{ formatTimestamp(recoveryResult.expiresAt) }} gültig. Er wird nicht gespeichert.</p>
        <code>{{ recoveryLink }}</code>
        <div class="dialog-actions">
          <button class="button secondary" type="button" @click="closeRecoveryResult">Schließen</button>
          <button class="button" type="button" @click="copyRecoveryLink">Link kopieren</button>
        </div>
      </section>
    </div>
  </div>
</template>
