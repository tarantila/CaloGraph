<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  open: boolean
  title: string
  description: string
  confirmLabel: string
  danger?: boolean
  pending?: boolean
  error?: string
}>()

const emit = defineEmits<{
  confirm: []
  close: []
}>()

const dialog = ref<HTMLElement | null>(null)
const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null

onMounted(async () => {
  await nextTick()
  dialog.value?.focus()
})
onBeforeUnmount(() => returnFocus?.focus())
</script>

<template>
  <div
    v-if="props.open"
    class="dialog-backdrop"
    role="presentation"
    @click.self="emit('close')"
    @keydown.esc="emit('close')"
  >
    <section
      ref="dialog"
      class="card action-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      tabindex="-1"
    >
      <h2 id="confirm-dialog-title">{{ props.title }}</h2>
      <p>{{ props.description }}</p>
      <div v-if="props.error" class="error" role="alert">{{ props.error }}</div>
      <div class="dialog-actions">
        <button class="button secondary" type="button" :disabled="props.pending" @click="emit('close')">Abbrechen</button>
        <button
          class="button"
          :class="{ danger: props.danger }"
          type="button"
          :disabled="props.pending"
          @click="emit('confirm')"
        >
          {{ props.confirmLabel }}
        </button>
      </div>
    </section>
  </div>
</template>
