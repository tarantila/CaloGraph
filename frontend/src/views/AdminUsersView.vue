<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import UserManagement from '../components/UserManagement.vue'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { User } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const users = ref<User[]>([])
const error = ref('')
const message = ref('')

async function load() {
  try {
    users.value = await api<User[]>('/admin/users')
    error.value = ''
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  }
}

onMounted(load)
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminUi.usersTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.usersDescription') }}</p>
    <p v-if="message" class="setup-notice" role="status">{{ message }}</p>
    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <section v-if="auth.user" class="card admin-panel" aria-labelledby="admin-users-panel">
      <div class="admin-panel-header">
        <div>
          <h2 id="admin-users-panel">{{ t('adminUi.users') }}</h2>
          <p>{{ users.length }} {{ t('adminUi.users').toLowerCase() }}</p>
        </div>
      </div>
      <UserManagement
        :users="users"
        :current-user-id="auth.user.id"
        @refresh="load"
        @message="message = $event"
        @error="error = $event"
      />
    </section>
  </section>
</template>
