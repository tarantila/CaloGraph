<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { formatGermanDateTime } from '../date-format'
import { i18n } from '../i18n'

interface Invitation { id: string; created_at: string; expires_at: string; used_at: string | null; revoked_at: string | null }
const t = i18n.global.t.bind(i18n.global)
const invitations = ref<Invitation[]>([])
const invitationUrl = ref('')
const error = ref('')
const message = ref('')

async function load() {
  try {
    invitations.value = await api<Invitation[]>('/admin/invitations')
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  }
}

async function createInvitation() {
  try {
    const result = await api<{ invitation_url: string }>('/users/invitations', {
      method: 'POST',
      body: JSON.stringify({ expires_in_days: 7 }),
    })
    invitationUrl.value = result.invitation_url
    message.value = t('adminUi.invitationCreated')
    await load()
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  }
}

async function copyInvitation() {
  await navigator.clipboard.writeText(invitationUrl.value)
  message.value = t('adminUi.invitationCopied')
}

async function revokeInvitation(id: string) {
  try {
    await api(`/users/invitations/${id}`, { method: 'DELETE' })
    message.value = t('adminUi.invitationRevoked')
    await load()
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  }
}

onMounted(load)
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminUi.invitationsTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.invitationsDescription') }}</p>
    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <p v-if="message" class="setup-notice" role="status">{{ message }}</p>
    <section class="card admin-panel" aria-labelledby="admin-invitations-panel">
      <div class="admin-panel-header">
        <div>
          <h2 id="admin-invitations-panel">{{ t('adminUi.invitations') }}</h2>
          <p>{{ invitations.length }} {{ t('adminUi.invitations').toLowerCase() }}</p>
        </div>
        <button class="button compact-action" type="button" @click="createInvitation">{{ t('adminUi.createInvitation') }}</button>
      </div>
      <div v-if="invitationUrl" class="invitation-result">
        <strong>{{ t('adminUi.shareInvitation') }}</strong>
        <code>{{ invitationUrl }}</code>
        <button class="button secondary" type="button" @click="copyInvitation">{{ t('common.copy') }}</button>
      </div>
      <div class="table-scroll admin-desktop-table">
        <table>
          <thead><tr><th>{{ t('adminUi.created') }}</th><th>{{ t('adminUi.expires') }}</th><th>{{ t('adminUi.status') }}</th><th>{{ t('common.actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in invitations" :key="item.id">
              <td>{{ formatGermanDateTime(item.created_at) }}</td>
              <td>{{ formatGermanDateTime(item.expires_at) }}</td>
              <td>{{ item.used_at ? t('adminUi.used') : item.revoked_at ? t('adminUi.revoked') : t('adminUi.open') }}</td>
              <td><button v-if="!item.used_at && !item.revoked_at" class="text-button" type="button" @click="revokeInvitation(item.id)">{{ t('adminUi.revoke') }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="mobile-invitation-list">
        <article v-for="item in invitations" :key="item.id" class="mobile-invitation-card">
          <div class="mobile-invitation-card-header">
            <strong>{{ item.used_at ? t('adminUi.used') : item.revoked_at ? t('adminUi.revoked') : t('adminUi.open') }}</strong>
            <button v-if="!item.used_at && !item.revoked_at" class="text-button" type="button" @click="revokeInvitation(item.id)">{{ t('adminUi.revoke') }}</button>
          </div>
          <dl>
            <div><dt>{{ t('adminUi.created') }}</dt><dd>{{ formatGermanDateTime(item.created_at) }}</dd></div>
            <div><dt>{{ t('adminUi.expires') }}</dt><dd>{{ formatGermanDateTime(item.expires_at) }}</dd></div>
            <div v-if="item.used_at"><dt>{{ t('adminUi.used') }}</dt><dd>{{ formatGermanDateTime(item.used_at) }}</dd></div>
          </dl>
        </article>
      </div>
    </section>
  </section>
</template>
