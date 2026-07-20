<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api, ensureCsrfToken } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import type { ImportBatch } from '../types'

const imports = ref<ImportBatch[]>([])
const selected = ref<File | null>(null)
const progress = ref(0)
const uploading = ref(false)
const message = ref('')

async function load() { imports.value = await api<ImportBatch[]>('/imports') }
onMounted(load)

function selectFile(event: Event) {
  selected.value = (event.target as HTMLInputElement).files?.[0] ?? null
  progress.value = 0
  message.value = ''
}

async function upload() {
  if (!selected.value) return
  uploading.value = true
  const csrf = await ensureCsrfToken()
  const form = new FormData()
  form.append('file', selected.value)
  const xhr = new XMLHttpRequest()
  xhr.open('POST', '/api/v1/import/apple-health/file')
  xhr.setRequestHeader('X-CSRF-Token', csrf)
  xhr.withCredentials = true
  xhr.upload.onprogress = (event) => { if (event.lengthComputable) progress.value = Math.round((event.loaded / event.total) * 100) }
  xhr.onload = async () => {
    uploading.value = false
    if (xhr.status >= 200 && xhr.status < 300) {
      const result = JSON.parse(xhr.responseText) as { inserted: number; updated: number; skipped: number }
      message.value = `${result.inserted} neu, ${result.updated} aktualisiert, ${result.skipped} übersprungen.`
      await load()
    } else {
      try { message.value = (JSON.parse(xhr.responseText) as { detail: string }).detail } catch { message.value = 'Upload ist fehlgeschlagen.' }
    }
  }
  xhr.onerror = () => { uploading.value = false; message.value = 'Netzwerkfehler beim Upload.' }
  xhr.send(form)
}
</script>

<template>
  <div class="page-heading"><div><h1>Importe</h1><p>Historische Apple-Health-Exporte und vergangene Importläufe.</p></div></div>
  <section class="card form-card">
    <h2>Historischen Export importieren</h2>
    <p>Akzeptiert die originale <code>export.xml</code> oder ein Apple-Health-ZIP. Die Datei bleibt auf deiner Infrastruktur.</p>
    <div class="filters">
      <label class="field">XML- oder ZIP-Datei<input type="file" accept=".xml,.zip,application/xml,application/zip" @change="selectFile" /></label>
      <button class="button" type="button" :disabled="!selected || uploading" @click="upload">{{ uploading ? `Upload ${progress} %` : 'Importieren' }}</button>
    </div>
    <progress v-if="uploading" :value="progress" max="100" style="width: 100%; margin-top: 1rem">{{ progress }} %</progress>
    <p v-if="message" role="status">{{ message }}</p>
  </section>
  <section class="card table-card"><div class="table-scroll"><table>
    <thead><tr><th>Zeitpunkt</th><th>Quelle</th><th>Status</th><th class="number">Empfangen</th><th class="number">Neu</th><th class="number">Aktualisiert</th><th class="number">Übersprungen</th><th class="number">Fehler</th></tr></thead>
    <tbody><tr v-for="batch in imports" :key="batch.id"><td>{{ new Date(batch.started_at).toLocaleString('de-DE') }}</td><td>{{ batch.source_type }}</td><td><StatusBadge :status="batch.status" /></td><td class="number">{{ batch.received }}</td><td class="number">{{ batch.inserted }}</td><td class="number">{{ batch.updated }}</td><td class="number">{{ batch.skipped }}</td><td class="number">{{ batch.failed }}</td></tr><tr v-if="!imports.length"><td colspan="8" class="empty">Noch keine Importläufe.</td></tr></tbody>
  </table></div></section>
</template>

