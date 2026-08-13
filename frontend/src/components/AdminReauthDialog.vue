<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { ApiError, localizeApiError } from '../api'
import { i18n } from '../i18n'

export interface AdminReauthentication {
  current_password: string
  code: string | null
  confirm_username?: string
}

const props = defineProps<{
  open: boolean
  title: string
  description: string
  submitLabel: string
  perform: (payload: AdminReauthentication) => Promise<void>
  danger?: boolean
  confirmUsername?: string
}>()

const t = i18n.global.t.bind(i18n.global)
const emit = defineEmits<{ close: []; completed: [] }>()
const currentPassword = ref('')
const code = ref('')
const confirmation = ref('')
const error = ref('')
const submitting = ref(false)
const passwordInput = ref<HTMLInputElement | null>(null)
const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
const confirmationMatches = computed(() => props.confirmUsername == null || confirmation.value === props.confirmUsername)
const canSubmit = computed(() => Boolean(currentPassword.value) && confirmationMatches.value && !submitting.value)

function clearCredentials() {
  currentPassword.value = ''
  code.value = ''
  confirmation.value = ''
  error.value = ''
}
function close() {
  if (submitting.value) return
  clearCredentials()
  emit('close')
}
async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  error.value = ''
  try {
    await props.perform({
      current_password: currentPassword.value,
      code: code.value.trim() || null,
      ...(props.confirmUsername == null ? {} : { confirm_username: confirmation.value }),
    })
    clearCredentials()
    emit('completed')
    emit('close')
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause, 'errors.generic') : t('errors.generic')
  } finally {
    submitting.value = false
  }
}
onMounted(async () => {
  await nextTick()
  passwordInput.value?.focus()
})
onBeforeUnmount(() => returnFocus?.focus())
watch(
  () => props.open,
  async (open) => {
    if (!open) {
      clearCredentials()
      return
    }
    await nextTick()
    passwordInput.value?.focus()
  },
)
</script>

<template>
  <div
    v-if="props.open"
    class="dialog-backdrop"
    role="presentation"
    @click.self="close"
    @keydown.esc="close"
  >
    <section class="card action-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-reauth-title">
      <h2 id="admin-reauth-title">{{ props.title }}</h2>
      <p>{{ props.description }}</p>
      <form @submit.prevent="submit">
        <label class="field">
          {{ t('adminReauth.password') }}
          <input ref="passwordInput" v-model="currentPassword" type="password" autocomplete="current-password" required />
        </label>
        <label class="field">
          {{ t('adminReauth.code') }} <span class="muted">({{ t('adminReauth.optional') }})</span>
          <input v-model="code" inputmode="text" autocomplete="one-time-code" autocapitalize="characters" />
        </label>
        <label v-if="props.confirmUsername" class="field">
          {{ t('adminReauth.confirm', { username: props.confirmUsername }) }}
          <input v-model="confirmation" autocomplete="off" spellcheck="false" required />
        </label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <div class="dialog-actions">
          <button class="button secondary" type="button" :disabled="submitting" @click="close">{{ t('common.cancel') }}</button>
          <button class="button" :class="{ danger: props.danger }" type="submit" :disabled="!canSubmit">
            {{ submitting ? t('adminReauth.running') : props.submitLabel }}
          </button>
        </div>
      </form>
    </section>
  </div>
</template>
