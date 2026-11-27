<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { PhChatCircleText, PhChartLineUp, PhArrowUp, PhStop, PhPlus, PhPaperclip, PhMicrophone, PhX, PhTrash, PhUser } from '@phosphor-icons/vue'
import { api, ApiError } from '../api'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { useDictation } from '../composables/useDictation'
import { useAuthStore } from '../stores/auth'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import { CONSENT_POLICY_VERSION } from '../generated/ai-contracts'
import type { AIConsentPut, AIConsentView, AttachmentView, ConversationPage, ConversationPut, ConversationView, ExpertSetup, ListCursor, RunPage, RunPut, RunView } from '../generated/ai-contracts'
import { i18n } from '../i18n'
import { clientUuid } from '../uuid'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ownerIdentity = captureAuthIdentity()
type Mode = ConversationPut['mode']
const setup = ref<ExpertSetup | null>(null)
const conversations = ref<ConversationView[]>([])
const nextConversation = ref<ListCursor | null>(null)
const conversation = ref<ConversationView | null>(null)
const turns = ref<RunView[]>([])
const nextTurn = ref<ListCursor | null>(null)
const draft = ref('')
const draftKind = ref<Mode>('chat')
const busy = ref(false)
const loading = ref(true)
const error = ref('')
const permission = ref(false)
const permissionError = ref('')
const deleteTarget = ref<ConversationView | null>(null)
const selectedFiles = ref<AttachmentView[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const fileDragActive = ref(false)
const composerInput = ref<HTMLTextAreaElement | null>(null)
let composerResizeObserver: ResizeObserver | null = null
const uploading = ref(false)
const dictationConsent = ref(false)
let dictationAllowed = false
const reusedAttachmentIds = new Set<string>()
const pendingUpload = ref<{ file: File; id: string; conversationId: string } | null>(null)
const dictation = useDictation(text => { if (sessionCurrent()) draft.value = text })
const requiresDocuments = computed(() => Boolean(selectedFiles.value.length || turns.value.some(turn => turn.state === 'succeeded' && turn.attachments?.length)))
const transcript = ref<HTMLElement | null>(null)
function resizeComposer(): void {
  const input = composerInput.value
  if (!input) return
  input.style.height = 'auto'
  const contentHeight = input.value ? input.scrollHeight : 40
  const maxHeight = 128
  input.style.height = `${Math.min(Math.max(contentHeight, 40), maxHeight)}px`
  input.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden'
}
watch(draft, () => { void nextTick(resizeComposer) }, { flush: 'post' })
watch(composerInput, input => {
  composerResizeObserver?.disconnect()
  composerResizeObserver = null
  if (typeof ResizeObserver !== 'undefined' && input?.parentElement) {
    composerResizeObserver = new ResizeObserver(resizeComposer)
    composerResizeObserver.observe(input.parentElement)
  }
  void nextTick(resizeComposer)
}, { flush: 'post' })
let pending: { kind: Mode; message: string } | null = null
const submission = ref<{ id: string; conversation: string; payload: RunPut } | null>(null)
let permissionIds: { key: string; chat: string; analysis: string } | null = null
let creation: { id: string; mode: Mode } | null = null
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let openingConversation: { id: string; promise: Promise<void> } | null = null
let fileDragDepth = 0
function sessionCurrent(): boolean { return !disposed && isAuthIdentityCurrent(ownerIdentity) }
function current(token = generation): boolean { return sessionCurrent() && token === generation }
function resetFileDrag(): void { fileDragDepth = 0; fileDragActive.value = false }
function reloadSession(): void { window.location.reload() }
watch(() => auth.user?.id, () => {
  if (sessionCurrent()) return
  dictation.stop(); stopPolling(); generation++
  resetFileDrag()
  dictationConsent.value = false
  setup.value = null; conversation.value = null; conversations.value = []; turns.value = []
  selectedFiles.value = []; pendingUpload.value = null; draft.value = ''; permission.value = false; permissionIds = null; pending = null; creation = null; deleteTarget.value = null; submission.value = null; busy.value = false; uploading.value = false
  error.value = t('expertChat.sessionChanged')
})
const active = computed(() => turns.value.find(turn => turn.state === 'queued' || turn.state === 'running'))
const provider = computed(() => setup.value?.connection ? t(`accountAISetup.providers.${setup.value.connection.provider_kind}`) : '')
const attachmentNames = computed(() => new Map(
  turns.value.flatMap(turn => turn.attachments ?? []).map(file => [file.id.toLowerCase(), file.file_name]),
))
function displayAssistantText(value: string): string {
  return value.replace(/attachment:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/gi, (_reference, id: string) =>
    attachmentNames.value.get(id.toLowerCase()) ?? t('expertChat.documentEvidence'),
  )
}
const safeFailureKeys: Record<string, string> = {
  invalid_request: 'invalidRequest', invalid_response: 'invalidResponse', output_limit: 'outputLimit',
  timeout: 'timeout', provider_deadline: 'timeout', rate_limited: 'rateLimited',
  context_too_large: 'contextTooLarge', authentication: 'authentication',
  credential_unavailable: 'credentialUnavailable', provider_unavailable: 'providerUnavailable',
}
function safeFailure(turn: RunView): string {
  if (turn.state !== 'failed' || !turn.error_code) return ''
  const key = safeFailureKeys[turn.error_code]
  return key ? t(`expertChat.failures.${key}`) : ''
}
const permissionDescription = computed(() => t('expertChat.permissionDescription', {
  provider: provider.value, model: setup.value?.connection?.model_id || '',
  domains: setup.value?.preferences.data_domains.map(domain => t(`expertSettings.domains.${domain}.label`)).join(', ') ?? '',
  names: setup.value?.preferences.include_meal_names ? t('expertChat.withNames') : '',
  destination: setup.value?.connection?.base_url ?? '',
  documents: requiresDocuments.value ? t('expertChat.documentPermission') : '',
}))
function cursorQuery(cursor: ListCursor | null): string {
  return cursor ? `&before_created_at=${encodeURIComponent(cursor.created_at)}&before_id=${cursor.id}` : ''
}
function stopPolling(): void { if (timer) clearTimeout(timer); timer = undefined }
function mergeTurn(turn: RunView): void {
  const old = turns.value.find(item => item.id === turn.id)
  // A response already in flight must not undo a completed cancellation.
  if (old && !['queued', 'running'].includes(old.state) && ['queued', 'running'].includes(turn.state)) return
  turns.value = [...turns.value.filter(item => item.id !== turn.id), turn].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
}
function isRunResponse(value: unknown, request: { id: string; conversation: string }): value is RunView {
  if (!value || typeof value !== 'object') return false
  const run = value as Partial<RunView>
  return run.id === request.id && run.conversation_id === request.conversation
    && ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.state ?? '')
}
async function refreshConversationMetadata(id: string): Promise<void> {
  const token = generation
  try {
    const item = await api<ConversationView>(`/expert/conversations/${id}`)
    if (current(token) && conversation.value?.id === id) conversation.value = item
  } catch { /* The persisted conversation can still be reopened later. */ }
}
async function scrollToLatest(token = generation): Promise<void> { await nextTick(); if (current(token)) transcript.value?.scrollTo?.({ top: transcript.value.scrollHeight, behavior: 'smooth' }) }
function poll(turn: RunView): void {
  stopPolling()
  if (!['queued', 'running'].includes(turn.state)) return
  const token = generation
  timer = setTimeout(async () => {
    try {
      const fresh = await api<RunView>(`/expert/runs/${turn.id}`)
      if (!sessionCurrent() || token !== generation) return
      mergeTurn(fresh); poll(fresh)
      if (fresh.state === 'succeeded') await refreshConversationMetadata(fresh.conversation_id)
      if (fresh.answer) await scrollToLatest(token)
    } catch { if (sessionCurrent() && token === generation) error.value = t('expertChat.pollFailed') }
  }, 700)
}
async function refreshSetup(mode: Mode, token = generation): Promise<ExpertSetup> {
  const value = await api<ExpertSetup>(`/expert/setup?mode=${mode}&include_attachments=${requiresDocuments.value}`)
  if (current(token)) setup.value = value
  return value
}
async function loadConversations(more = false): Promise<void> {
  const token = generation
  if (!current(token)) return
  const result = await api<ConversationPage>(`/expert/conversations?limit=20${cursorQuery(more ? nextConversation.value : null)}`)
  if (!current(token)) return
  conversations.value = more ? [...conversations.value, ...result.items] : [...result.items]
  nextConversation.value = result.next_before
}
function openConversation(id: string): Promise<void> {
  if (openingConversation?.id === id) return openingConversation.promise
  const promise = loadConversation(id)
  const operation = { id, promise }
  openingConversation = operation
  void promise.finally(() => { if (openingConversation === operation) openingConversation = null })
  return promise
}
async function loadConversation(id: string): Promise<void> {
  if (!sessionCurrent()) return
  const token = ++generation
  stopPolling(); dictation.stop(); resetFileDrag(); selectedFiles.value = []; reusedAttachmentIds.clear(); pendingUpload.value = null; error.value = ''; turns.value = []; conversation.value = null; submission.value = null; pending = null; permission.value = false; busy.value = false; uploading.value = false
  loading.value = true
  try {
    const [item, firstPage, drafts] = await Promise.all([
      api<ConversationView>(`/expert/conversations/${id}`), api<RunPage>(`/expert/conversations/${id}/turns?limit=20`),
      api<AttachmentView[]>(`/expert/conversations/${id}/attachments`),
    ])
    if (!sessionCurrent() || token !== generation) return
    const allTurns = [...firstPage.items]
    const seenCursors = new Set<string>()
    let cursor = firstPage.next_before
    while (cursor && allTurns.length < 200) {
      const key = `${cursor.created_at}\u0000${cursor.id}`
      if (seenCursors.has(key)) throw new Error('Conversation history cursor did not advance')
      seenCursors.add(key)
      const page = await api<RunPage>(`/expert/conversations/${id}/turns?limit=20${cursorQuery(cursor)}`)
      if (!sessionCurrent() || token !== generation) return
      if (!page.items.length && page.next_before) throw new Error('Conversation history cursor did not advance')
      allTurns.push(...page.items)
      if (allTurns.length > 200) allTurns.length = 200
      if (page.next_before && page.next_before.created_at === cursor.created_at && page.next_before.id === cursor.id) throw new Error('Conversation history cursor did not advance')
      cursor = page.next_before
    }
    if (cursor) throw new Error('Conversation history exceeds its documented bound')
    conversation.value = item; selectedFiles.value = [...drafts]; turns.value = [...allTurns].reverse(); nextTurn.value = null
    for (const turn of allTurns) for (const attachment of turn.attachments ?? []) reusedAttachmentIds.add(attachment.id)
    if (active.value) poll(active.value)
  } catch { if (sessionCurrent() && token === generation) error.value = t('expertChat.loadFailed') }
  finally { if (sessionCurrent() && token === generation) { loading.value = false; void scrollToLatest() } }
}
watch(() => route.query.conversation, id => {
  if (typeof id === 'string') { if (conversation.value?.id !== id) void openConversation(id) }
  else { generation++; const token = generation; stopPolling(); dictation.stop(); resetFileDrag(); selectedFiles.value = []; reusedAttachmentIds.clear(); pendingUpload.value = null; conversation.value = null; turns.value = []; draft.value = ''; submission.value = null; permission.value = false; busy.value = false; uploading.value = false; void loadConversations().catch(() => { if (current(token)) error.value = t('expertChat.loadFailed') }) }
})
async function start(mode: Mode): Promise<void> {
  const token = generation
  let createdConversationId: string | null = null
  let createdRouteGeneration: number | null = null
  if (!current(token)) return
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    if (!creation || creation.mode !== mode) creation = { id: clientUuid(), mode }
    const item = await api<ConversationView>(`/expert/conversations/${creation.id}`, { method: 'PUT', body: JSON.stringify({ mode }) })
    if (!current(token)) return
    if (item.id !== creation.id || item.mode !== mode) throw new Error('Conversation response did not match the request')
    creation = null
    await router.push({ query: { conversation: item.id } })
    await openConversation(item.id)
    const routeToken = generation
    await loadConversations()
    if (!current(routeToken) || conversation.value?.id !== item.id) return
    createdConversationId = item.id
    createdRouteGeneration = routeToken
  } catch { if (current(token)) error.value = t('expertChat.startFailed') }
  finally { if (current(token)) busy.value = false }
  if (mode === 'analysis' && createdConversationId && createdRouteGeneration !== null
    && current(createdRouteGeneration) && conversation.value?.id === createdConversationId
    && route.query.conversation === createdConversationId) {
    await requestSend('analysis', t('expertChat.analysisPrompt'))
  }
}
async function requestSend(kind: Mode, message: string): Promise<void> {
  const token = generation
  if (!current(token)) return
  if (busy.value || uploading.value || pendingUpload.value || dictation.recording.value || active.value || (!message.trim() && !selectedFiles.value.length) || !conversation.value) return
  if (!message.trim()) message = t('expertChat.documentPrompt')
  if (submission.value) return // Unknown delivery must be resolved using the original request ID.
  busy.value = true; error.value = ''
  try {
    const state = await refreshSetup(kind, token)
    if (!current(token)) return
    if (!state.generation_available || !state.connection) { error.value = t('expertChat.noProvider'); return }
    pending = { kind, message }
    if (!state.consent) { permissionError.value = ''; permission.value = true; return }
    await submit(state)
  } catch { if (current(token)) error.value = t('expertChat.sendFailed') }
  finally { if (current(token)) busy.value = false }
}
function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  // Keep newlines available during IME composition, including browsers that
  // report the legacy 229 key code instead of `isComposing`.
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (event.repeat) return
  void requestSend(draftKind.value, draft.value)
}
async function acceptPermission(): Promise<void> {
  const token = generation
  if (!current(token)) return
  if (busy.value || !pending || !setup.value?.connection) return
  busy.value = true; permissionError.value = ''
  const expected = setup.value
  const connection = expected.connection!
  const key = `${connection.id}:${connection.configuration_version}:${expected.preferences.configuration_version}:${requiresDocuments.value}`
  if (!permissionIds || permissionIds.key !== key) permissionIds = { key, chat: clientUuid(), analysis: clientUuid() }
  const ids = permissionIds
  try {
    // The dialog explicitly covers both chats and analyses with the configured categories.
    for (const mode of ['chat', 'analysis'] as const) {
      if (!current(token)) return
      const state = await refreshSetup(mode, token)
      if (!current(token)) return
      if (state.connection?.id !== connection.id || state.connection.configuration_version !== connection.configuration_version || state.preferences.configuration_version !== expected.preferences.configuration_version) throw new Error('Configuration changed')
      if (state.consent) continue
      const payload: AIConsentPut = {
        connection_id: connection.id, configuration_version: connection.configuration_version,
        purpose: mode === 'chat' ? 'nutrition_chat' : 'nutrition_check_in',
        data_domains: expected.preferences.data_domains, include_meal_names: expected.preferences.include_meal_names, include_attachments: requiresDocuments.value,
        history: { mode: 'all_available', start: null, end: null }, policy_version: CONSENT_POLICY_VERSION,
      }
      try {
        const saved = await api<AIConsentView>(`/settings/ai/consents/${ids[mode]}`, { method: 'PUT', body: JSON.stringify(payload) })
        if (!current(token)) return
        if (saved.state !== 'active') { permissionIds = null; throw new Error('Permission no longer active') }
      }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.problemType !== 'urn:calograph:problem:ai-consent-already-exists') throw cause
        if (!(await refreshSetup(mode, token)).consent || !current(token)) throw cause
      }
    }
    const state = await refreshSetup(pending.kind, token)
    if (!current(token)) return
    if (!state.consent) throw new Error('Permission unavailable')
    permission.value = false; permissionIds = null
    await submit(state)
  } catch { if (current(token)) permissionError.value = t('expertChat.permissionFailed') }
  finally { if (current(token)) busy.value = false }
}
async function submit(state: ExpertSetup): Promise<void> {
  if (!sessionCurrent()) return
  if (!conversation.value || !state.connection || !state.consent || !pending) return
  submission.value = { id: clientUuid(), conversation: conversation.value.id, payload: {
    kind: pending.kind, message: pending.message, connection_id: state.connection.id,
    connection_version: state.connection.configuration_version, preferences_version: state.preferences.configuration_version,
    consent_id: state.consent.id, attachment_ids: selectedFiles.value.map(item => item.id),
    ...(state.routing_configuration_version !== undefined ? { routing_configuration_version: state.routing_configuration_version } : {}),
  } }
  pending = null
  await deliver()
}
async function deliver(): Promise<void> {
  const token = generation
  if (!current(token)) return
  const request = submission.value
  if (!request) return
  try {
    const result = await api<RunView>(`/expert/conversations/${request.conversation}/turns/${request.id}`, { method: 'PUT', body: JSON.stringify(request.payload) })
    if (!current(token) || conversation.value?.id !== request.conversation) return
    if (!isRunResponse(result, request)) throw new Error('Run response did not match the request')
    mergeTurn(result); void refreshConversationMetadata(result.conversation_id); selectedFiles.value = []; reusedAttachmentIds.clear(); submission.value = null; draft.value = ''; draftKind.value = 'chat'; poll(result); await scrollToLatest()
  } catch (cause) {
    if (!current(token) || conversation.value?.id !== request.conversation) return
    if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
      submission.value = null; error.value = t('expertChat.sendFailed'); return
    }
    // A lost response can follow a charged provider request. Read first, never create another ID.
    try {
      const result = await api<RunView>(`/expert/runs/${request.id}`)
      if (!current(token) || conversation.value?.id !== request.conversation) return
      if (!isRunResponse(result, request)) throw new Error('Run status did not match the request')
      mergeTurn(result); void refreshConversationMetadata(result.conversation_id); selectedFiles.value = []; reusedAttachmentIds.clear(); submission.value = null; draft.value = ''; draftKind.value = 'chat'; poll(result)
    } catch { if (current(token)) error.value = t('expertChat.deliveryUnknown') }
  }
}
async function retryDelivery(): Promise<void> { const token = generation; busy.value = true; error.value = ''; try { await deliver() } finally { if (current(token)) busy.value = false } }
async function cancel(): Promise<void> {
  const token = generation
  if (!current(token)) return
  const item = active.value
  if (!item || busy.value) return
  busy.value = true; error.value = ''
  try { const result = await api<RunView>(`/expert/runs/${item.id}/cancel`, { method: 'PUT' }); if (!current(token)) return; mergeTurn(result); stopPolling() }
  catch { if (current(token)) error.value = t('expertChat.cancelFailed') }
  finally { if (current(token)) busy.value = false }
}
async function moreTurns(): Promise<void> {
  const token = generation
  if (!conversation.value || !nextTurn.value || busy.value) return
  const id = conversation.value.id
  busy.value = true
  try {
    const page = await api<RunPage>(`/expert/conversations/${id}/turns?limit=20${cursorQuery(nextTurn.value)}`)
    if (current(token) && conversation.value?.id === id) { for (const turn of page.items) mergeTurn(turn); nextTurn.value = page.next_before }
  } catch { if (current(token)) error.value = t('expertChat.loadFailed') }
  finally { if (current(token)) busy.value = false }
}
async function removeConversation(): Promise<void> {
  const token = generation
  if (!current(token)) return
  const target = deleteTarget.value
  if (!target || busy.value) return
  busy.value = true
  try {
    await api(`/expert/conversations/${target.id}`, { method: 'DELETE' })
    if (!current(token)) return
    deleteTarget.value = null
    if (conversation.value?.id === target.id) await router.push({ query: {} })
    await loadConversations()
  } catch { if (current(token)) error.value = t('expertChat.deleteFailed') }
  finally { if (current(token)) busy.value = false }
}
function conversationTitle(item: ConversationView): string {
  if (item.mode === 'analysis') return t('expertChat.analysisDated', { date: new Date(item.created_at).toLocaleDateString(i18n.global.locale.value, { timeZone: auth.user?.timezone ?? 'UTC' }) })
  return item.title || t('expertChat.chatTitle')
}
function attachFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  void uploadFiles(files)
}
function transferHasFiles(transfer: DataTransfer | null): boolean {
  if (!transfer) return false
  return Array.from(transfer.types).includes('Files')
    || Array.from(transfer.items).some(item => item.kind === 'file')
    || transfer.files.length > 0
}
function canUploadFiles(): boolean {
  return current() && Boolean(conversation.value) && !busy.value && !uploading.value
    && !pendingUpload.value && !active.value && !submission.value && !dictation.recording.value
}
function handleFileDragEnter(event: DragEvent): void {
  if (!transferHasFiles(event.dataTransfer)) return
  event.preventDefault()
  if (!canUploadFiles()) return
  fileDragDepth++
  fileDragActive.value = true
}
function handleFileDragOver(event: DragEvent): void {
  if (!transferHasFiles(event.dataTransfer)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = canUploadFiles() ? 'copy' : 'none'
  if (canUploadFiles()) fileDragActive.value = true
}
function handleFileDragLeave(event: DragEvent): void {
  if (!fileDragActive.value) return
  event.preventDefault()
  fileDragDepth = Math.max(0, fileDragDepth - 1)
  if (fileDragDepth === 0) fileDragActive.value = false
}
function handleFileDrop(event: DragEvent): void {
  if (!transferHasFiles(event.dataTransfer)) return
  event.preventDefault()
  const files = Array.from(event.dataTransfer?.files ?? [])
  resetFileDrag()
  if (files.length) void uploadFiles(files)
}
async function uploadFiles(files: readonly File[]): Promise<void> {
  const token = generation
  const target = conversation.value
  if (!files.length || !target || !canUploadFiles()) return
  uploading.value = true; error.value = ''
  try {
    const existingIds = new Set(turns.value.flatMap(turn => turn.attachments ?? []).map(file => file.id))
    selectedFiles.value.forEach(file => existingIds.add(file.id))
    if (existingIds.size + files.length > 20) { error.value = t('expertChat.fileLimit'); return }
    for (const file of files) {
      if (!current(token)) return
      const media = file.type || ({ pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' } as Record<string, string>)[file.name.split('.').at(-1)?.toLowerCase() ?? '']
      if (!media || !['application/pdf', 'image/png', 'image/jpeg', 'image/webp'].includes(media) || file.size > 4 * 1024 * 1024 || !file.size) { error.value = t('expertChat.fileInvalid'); break }
      pendingUpload.value = { file, id: clientUuid(), conversationId: target.id }
      await uploadPending()
      if (!current(token) || conversation.value?.id !== target.id) return
      if (pendingUpload.value) break
    }
  } catch { if (current(token) && conversation.value?.id === target.id) error.value = t('expertChat.fileFailed') }
  finally { if (current(token)) uploading.value = false }
}
async function uploadPending(): Promise<void> {
  const token = generation
  if (!current(token)) return
  const item = pendingUpload.value
  if (!item) return
  uploading.value = true; error.value = ''
  try {
    const media = item.file.type || ({ pdf: 'application/pdf', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp' } as Record<string, string>)[item.file.name.split('.').at(-1)?.toLowerCase() ?? ''] || ''
    const result = await api<AttachmentView>(`/expert/conversations/${item.conversationId}/attachments/${item.id}`, { method: 'PUT', headers: { 'Content-Type': media, 'X-Attachment-Filename': encodeURIComponent(Array.from(item.file.name).slice(0, 120).join('')) }, body: item.file })
    if (!current(token) || conversation.value?.id !== item.conversationId) return
    selectedFiles.value = [...selectedFiles.value.filter(file => file.id !== result.id), result]
    pendingUpload.value = null
  } catch (cause) {
    if (current(token) && conversation.value?.id === item.conversationId) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500 && ![408, 429].includes(cause.status) && (cause.status !== 409 || ['urn:calograph:problem:expert-attachment-limit', 'urn:calograph:problem:expert-attachment-conflict'].includes(cause.problemType ?? ''))) pendingUpload.value = null
      error.value = t('expertChat.fileFailed')
    }
  }
  finally { if (current(token)) uploading.value = false }
}
async function removeAttachment(item: AttachmentView): Promise<void> {
  const token = generation
  if (!current(token)) return
  if (!conversation.value || uploading.value || busy.value) return
  if (reusedAttachmentIds.has(item.id)) {
    selectedFiles.value = selectedFiles.value.filter(file => file.id !== item.id); reusedAttachmentIds.delete(item.id); return
  }
  uploading.value = true; error.value = ''
  try {
    await api(`/expert/conversations/${conversation.value.id}/attachments/${item.id}`, { method: 'DELETE' })
    if (!current(token)) return
    selectedFiles.value = selectedFiles.value.filter(file => file.id !== item.id)
  } catch { if (current(token)) error.value = t('expertChat.fileFailed') }
  finally { if (current(token)) uploading.value = false }
}
function reuseTurn(turn: RunView): void {
  if (!sessionCurrent()) return
  if (busy.value || uploading.value || active.value || submission.value) return
  draft.value = turn.message
  draftKind.value = turn.kind
  selectedFiles.value = [...(turn.attachments ?? [])]
  reusedAttachmentIds.clear()
  for (const file of selectedFiles.value) reusedAttachmentIds.add(file.id)
  void nextTick(() => document.getElementById('expert-message')?.focus())
}
function toggleDictation(): void {
  if (dictation.recording.value) { dictation.stop(); return }
  if (!sessionCurrent()) return
  if (busy.value || uploading.value || active.value) return
  if (!dictationAllowed) { dictationConsent.value = true; return }
  dictation.start(draft.value, i18n.global.locale.value)
}
function allowDictation(): void {
  if (!sessionCurrent()) return
  dictationAllowed = true; dictationConsent.value = false
  dictation.start(draft.value, i18n.global.locale.value)
}
function evidenceLabel(id: string): string {
  const domain = id.split(':')[0]
  const dates = id.match(/\d{4}-\d{2}-\d{2}/g)
  return `${domain === 'attachment' ? t('expertChat.documentEvidence') : t(`expertSettings.domains.${domain}.label`, domain ?? '')}${dates ? ' · ' + dates.join(' – ') : ''}`
}
onMounted(async () => {
  const token = generation
  window.addEventListener('resize', resizeComposer)
  try { await Promise.all([refreshSetup('chat', token), loadConversations()]); if (current(token) && typeof route.query.conversation === 'string') await openConversation(route.query.conversation) }
  catch { if (current(token)) error.value = t('expertChat.loadFailed') }
  finally { if (current(token)) { loading.value = false; await nextTick(); if (current(token)) resizeComposer() } }
})
onBeforeUnmount(() => { disposed = true; generation++; resetFileDrag(); stopPolling(); composerResizeObserver?.disconnect(); window.removeEventListener('resize', resizeComposer) })
</script>

<template>
  <section class="expert-chat-page">
    <header class="page-heading page-heading--period"><div><h1>Nutrition Expert</h1><p>{{ t('expertChat.subtitle') }}</p></div></header>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <template v-else>
      <aside v-if="!setup?.generation_available" class="card expert-notice"><p>{{ t('expertChat.noProvider') }}</p></aside>
      <template v-if="!conversation">
        <div class="expert-entry">
          <button class="card entry-card" type="button" :disabled="busy" @click="start('analysis')"><PhChartLineUp :size="30" aria-hidden="true" /><strong>{{ t('expertChat.startAnalysis') }}</strong><span>{{ t('expertChat.analysisHelp') }}</span><span class="entry-action">{{ t('expertChat.startAnalysis') }} →</span></button>
          <button class="card entry-card" type="button" :disabled="busy" @click="start('chat')"><PhChatCircleText :size="30" aria-hidden="true" /><strong>{{ t('expertChat.startChat') }}</strong><span>{{ t('expertChat.chatHelp') }}</span><span class="entry-action">{{ t('expertChat.startChat') }} →</span></button>
        </div>
        <p class="expert-subtle">{{ t('expertChat.historyHelp') }}</p>
        <section v-if="conversations.length" class="card expert-history" aria-labelledby="expert-history-title"><h2 id="expert-history-title">{{ t('expertChat.historyTitle') }}</h2><article v-for="item in conversations" :key="item.id" class="history-item"><RouterLink :to="{ query: { conversation: item.id } }"><span>{{ conversationTitle(item) }}</span><time :datetime="item.created_at">{{ new Date(item.created_at).toLocaleString(i18n.global.locale.value, { timeZone: auth.user?.timezone ?? 'UTC' }) }}</time></RouterLink><button class="history-delete-button" type="button" :aria-label="t('expertChat.deleteNamed', { title: conversationTitle(item) })" :disabled="busy" @click="deleteTarget = item"><PhTrash :size="16" aria-hidden="true" /></button></article><button v-if="nextConversation" class="button secondary" @click="loadConversations(true)">{{ t('expertChat.more') }}</button></section>
      </template>
      <section v-else class="card expert-conversation" aria-labelledby="expert-conversation-title">
        <header class="conversation-header"><div><h2 id="expert-conversation-title">{{ conversationTitle(conversation) }}</h2><p v-if="setup?.connection" class="expert-subtle">{{ provider }} · {{ setup.connection.model_id }}</p></div><RouterLink class="button secondary conversation-new" :aria-label="t('expertChat.newChat')" :title="t('expertChat.newChat')" :to="{ query: {} }"><PhPlus :size="16" aria-hidden="true" /><span>{{ t('expertChat.newChat') }}</span></RouterLink></header>
        <div ref="transcript" class="expert-transcript" role="log" tabindex="0" :aria-label="t('expertChat.transcript')" aria-live="polite" aria-relevant="additions text">
          <button v-if="nextTurn" class="button secondary" :disabled="busy" @click="moreTurns">{{ t('expertChat.olderMessages') }}</button>
          <div v-if="!turns.length" class="chat-empty"><PhChatCircleText :size="40" aria-hidden="true" /><p>{{ t('expertChat.empty') }}</p><button v-if="conversation.mode === 'analysis'" class="button" :disabled="busy" @click="requestSend('analysis', t('expertChat.analysisPrompt'))">{{ t('expertChat.startAnalysis') }}</button></div>
          <article v-for="turn in turns" :key="turn.id" class="expert-turn">
            <div class="message-row user-message-row"><div class="user-message"><span class="message-author">{{ t('expertChat.you') }}</span><p>{{ turn.message }}</p><ul v-if="turn.attachments?.length" class="sent-attachments"><li v-for="file in turn.attachments" :key="file.id"><PhPaperclip :size="14" aria-hidden="true" />{{ file.file_name }}</li></ul></div><span class="message-avatar user-avatar" aria-hidden="true"><PhUser :size="17" /></span></div>
            <div class="message-row assistant-message-row"><span class="message-avatar expert-avatar" aria-hidden="true"><PhChatCircleText :size="19" /></span><div class="assistant-message"><span class="message-author">Nutrition Expert <span class="expert-subtle">· {{ turn.model_id }}</span></span>
              <p v-if="turn.provider_attempts?.some(item => item.state === 'succeeded' && item.position > 0)" class="expert-subtle fallback-provenance">{{ t('providerRouting.usedProvider', { provider: t(`accountAISetup.providers.${turn.provider_kind}`) }) }}</p>
              <template v-if="turn.answer"><p class="answer-text">{{ displayAssistantText(turn.answer.text) }}</p><section v-for="(finding, index) in turn.answer.findings" :key="index" class="expert-finding"><h3>{{ t(`expertChat.findings.${finding.kind}`) }}</h3><p>{{ displayAssistantText(finding.text) }}</p><details v-if="finding.evidence_ids.length"><summary>{{ t('expertChat.evidence') }}</summary><ul><li v-for="id in finding.evidence_ids" :key="id">{{ evidenceLabel(id) }}</li></ul></details></section><aside v-if="turn.answer.limitations?.length" class="answer-limitations"><h3>{{ t('expertChat.limitations') }}</h3><ul><li v-for="item in turn.answer.limitations" :key="item">{{ displayAssistantText(item) }}</li></ul></aside><div v-if="turn.answer.follow_up?.length" class="follow-ups"><button v-for="question in turn.answer.follow_up" :key="question" type="button" :disabled="busy || !!active" @click="draft = displayAssistantText(question)">{{ displayAssistantText(question) }}</button></div></template>
              <p v-else :role="['queued', 'running'].includes(turn.state) ? 'status' : undefined">{{ t(`expertChat.states.${turn.state}`) }}</p>
              <p v-if="safeFailure(turn)" class="expert-subtle run-failure-reason">{{ safeFailure(turn) }}</p>
              <button v-if="['failed', 'interrupted', 'cancelled'].includes(turn.state)" class="button secondary" type="button" :disabled="busy || !!active || !!submission" @click="reuseTurn(turn)">{{ t('expertChat.reuseTurn') }}</button>
            </div></div>
          </article>
        </div>
        <form :class="['chat-composer', { 'file-drag-active': fileDragActive }]" @submit.prevent="requestSend(draftKind, draft)" @dragenter="handleFileDragEnter" @dragover="handleFileDragOver" @dragleave="handleFileDragLeave" @drop="handleFileDrop">
          <p v-if="fileDragActive" class="file-drop-hint" role="status">{{ t('expertChat.dropFiles') }}</p>
          <ul v-if="selectedFiles.length" class="draft-attachments"><li v-for="file in selectedFiles" :key="file.id"><PhPaperclip :size="14" aria-hidden="true" /><span>{{ file.file_name }}</span><button type="button" :aria-label="t('expertChat.removeFile', { name: file.file_name })" :disabled="busy || uploading" @click="removeAttachment(file)"><PhX :size="14" aria-hidden="true" /></button></li></ul>
          <label class="sr-only" for="expert-message">{{ t('expertChat.message') }}</label>
          <textarea id="expert-message" ref="composerInput" v-model="draft" name="expert-message" rows="1" maxlength="8000" :placeholder="t('expertChat.placeholder')" :readonly="dictation.recording.value" :disabled="busy || !!active || !!submission" @input="draftKind = 'chat'" @keydown="handleComposerKeydown" />
          <div class="composer-toolbar">
            <div class="composer-tools">
              <input ref="fileInput" class="sr-only" type="file" accept="application/pdf,image/png,image/jpeg,image/webp" multiple tabindex="-1" :aria-label="t('expertChat.attach')" @change="attachFiles" />
              <button class="button secondary icon-button composer-icon" type="button" :title="t('expertChat.fileHelp')" :aria-label="t('expertChat.attach')" :disabled="busy || uploading || !!active || !!submission || dictation.recording.value" @click="fileInput?.click()"><PhPaperclip :size="20" aria-hidden="true" /></button>
              <button class="button secondary icon-button composer-icon" type="button" :aria-label="dictation.recording.value ? t('expertChat.stopDictation') : t('expertChat.dictate')" :title="dictation.available.value ? t('expertChat.dictate') : t('expertChat.dictationUnsupported')" :aria-pressed="dictation.recording.value" :class="{ recording: dictation.recording.value }" :disabled="!dictation.available.value || busy || uploading || !!active || !!submission" @click="toggleDictation"><PhMicrophone :size="20" aria-hidden="true" /></button>
            </div>
            <button v-if="active" class="button secondary" type="button" :aria-label="t('expertChat.cancel')" :disabled="busy" @click="cancel"><PhStop :size="18" aria-hidden="true" />{{ t('expertChat.cancel') }}</button>
            <button v-else class="button composer-send" type="submit" :disabled="(!draft.trim() && !selectedFiles.length) || busy || uploading || !!pendingUpload || dictation.recording.value || !!submission || !setup?.generation_available"><PhArrowUp :size="18" aria-hidden="true" /><span>{{ t('expertChat.send') }}</span></button></div>
          <p v-if="uploading || dictation.recording.value" class="composer-status" role="status">{{ t(uploading ? 'expertChat.uploading' : 'expertChat.listening') }}</p>
        </form>
        <p v-if="dictation.failure.value" role="alert" class="account-form-error composer-message">{{ t(`expertChat.dictationErrors.${dictation.failure.value}`) }}</p>
        <footer class="conversation-footer"><button type="button" :disabled="busy || !!active" @click="deleteTarget = conversation">{{ t('expertChat.delete') }}</button></footer>
      </section>
    </template>
    <div v-if="error" class="account-form-error" role="alert"><p>{{ error }}</p><button v-if="!sessionCurrent()" class="button secondary" @click="reloadSession">{{ t('expertChat.reloadSession') }}</button><button v-if="pendingUpload" class="button secondary" :disabled="uploading" @click="uploadPending">{{ t('expertChat.retryUpload') }}</button><button v-if="submission" class="button secondary" :disabled="busy" @click="retryDelivery">{{ t('expertChat.retryDelivery') }}</button><button v-else-if="active" class="button secondary" @click="error = ''; poll(active)">{{ t('common.tryAgain') }}</button></div>
    <ConfirmDialog v-if="permission" :open="true" :title="t('expertChat.permissionTitle')" :description="permissionDescription" :confirm-label="t('expertChat.allowAndStart')" :pending="busy" :error="permissionError" @confirm="acceptPermission" @close="() => { if (!busy) { permission = false; pending = null } }" />
    <ConfirmDialog v-if="dictationConsent" :open="true" :title="t('expertChat.dictate')" :description="t('expertChat.dictationPrivacy')" :confirm-label="t('expertChat.startDictation')" @confirm="allowDictation" @close="dictationConsent = false" />
    <ConfirmDialog v-if="deleteTarget" :open="true" :title="t('expertChat.deleteNamed', { title: conversationTitle(deleteTarget) })" :description="t('expertChat.deleteHelp')" :confirm-label="t('expertChat.delete')" :danger="true" :pending="busy" @confirm="removeConversation" @close="deleteTarget = null" />
  </section>
</template>

<style scoped>
.expert-chat-page { display: grid; gap: 1.3rem; min-width: 0; overflow-wrap: anywhere; }
.page-heading { margin-bottom: 0; }
.page-heading > .button { min-height: var(--control-height); padding: .48rem .85rem; font-size: .8rem; }
.mobile-action-icon { display: none; }
.expert-entry { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.3rem; }
.entry-card { display: grid; gap: 1rem; text-align: start; color: var(--text); font: inherit; cursor: pointer; padding: 1.8rem; }
.entry-card > svg, .entry-action { color: var(--accent); }
.entry-card > strong { font-size: 1.25rem; }
.entry-card > span { line-height: 1.6; }
.entry-card:hover { border-color: var(--accent); }
.entry-action { font-weight: 600; margin-top: .5rem; }
.expert-subtle { color: var(--muted-strong); font-size: .8rem; line-height: 1.6; margin: 0; }
.expert-notice { border-inline-start: .25rem solid var(--accent); padding-block: .9rem; padding-inline: 1.3rem; }
.expert-notice p { margin: 0; }
.expert-history { padding: 1rem 1.2rem; }
.conversation-header .button, .chat-composer > button { display: inline-flex; align-items: center; gap: .5rem; }
.expert-history h2 { font-size: .9rem; font-weight: 650; letter-spacing: -.01em; margin: 0 0 .4rem; }
.conversation-header h2 { font-size: 1.05rem; margin: 0 0 .5rem; }
.history-item { display: flex; align-items: center; gap: .55rem; border-top: 1px solid var(--border); padding: .45rem 0; }
.history-item > a { display: flex; flex: 1; align-items: center; justify-content: space-between; gap: .75rem; min-width: 0; font-size: .8rem; }
.history-item > a > span { min-width: 0; overflow-wrap: anywhere; }
.history-item time { color: var(--muted-strong); font-size: .75rem; text-align: end; }
.history-delete-button { display: inline-flex; flex: 0 0 2.5rem; align-items: center; justify-content: center; width: 2.5rem; height: 2.5rem; padding: 0; border: 0; border-radius: .45rem; background: transparent; color: var(--danger); cursor: pointer; }
.history-delete-button:hover:not(:disabled) { background: transparent; color: var(--danger); }
.history-delete-button:disabled { color: var(--muted); cursor: not-allowed; }
.expert-conversation { width: min(100%, 59.4rem); height: calc(100dvh - 9rem); min-height: 22rem; margin-inline: auto; padding: .9rem 1rem .55rem; display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--border); background: var(--surface); box-shadow: none; }
.conversation-header { flex-shrink: 0; padding: .1rem .15rem .8rem; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.expert-transcript { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: transparent transparent; padding: 1rem .15rem; display: flex; flex-direction: column; gap: 1.5rem; font-size: .875rem; }
.expert-transcript:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: .35rem; }
.expert-transcript::-webkit-scrollbar, .chat-composer textarea::-webkit-scrollbar { width: .5rem; }
.expert-transcript::-webkit-scrollbar-track, .chat-composer textarea::-webkit-scrollbar-track { background: transparent; }
.expert-transcript::-webkit-scrollbar-thumb, .chat-composer textarea::-webkit-scrollbar-thumb { background: transparent; border-radius: 999px; }
.expert-transcript:hover, .expert-transcript:focus-within { scrollbar-color: var(--muted-strong) transparent; }
.expert-transcript:hover::-webkit-scrollbar-thumb, .expert-transcript:focus-within::-webkit-scrollbar-thumb,
.chat-composer textarea:hover::-webkit-scrollbar-thumb, .chat-composer textarea:focus::-webkit-scrollbar-thumb { background: var(--muted-strong); }
.chat-empty { margin: auto; text-align: center; max-width: 30rem; color: var(--muted-strong); padding: 2rem 0; }
.chat-empty svg { color: var(--accent); }
.expert-turn { display: grid; gap: 1.15rem; }
.message-row { display: flex; align-items: flex-start; gap: .6rem; min-width: 0; }
.user-message-row { justify-content: flex-end; }
.message-avatar { display: inline-flex; flex: 0 0 1.9rem; width: 1.9rem; height: 1.9rem; margin-top: .2rem; align-items: center; justify-content: center; border-radius: 50%; }
.message-avatar svg { display: block; }
.expert-avatar { background: var(--accent-soft); color: var(--accent); }
.user-avatar { background: var(--surface-soft); color: var(--muted-strong); }
.user-message { position: relative; min-width: 0; width: fit-content; max-width: min(calc(100% - 2.5rem), 42rem); background: var(--accent-soft); border: 1px solid var(--border); border-radius: 1.1rem .3rem 1.1rem 1.1rem; padding: .7rem .9rem; }
.user-message::after, .assistant-message::before { content: ""; position: absolute; top: .85rem; width: .45rem; height: .45rem; transform: rotate(45deg); }
.user-message::after { right: -.25rem; background: var(--accent-soft); border-top: 1px solid var(--border); border-right: 1px solid var(--border); }
.assistant-message::before { left: -.25rem; background: var(--surface-raised); border-left: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.message-author { font-size: .75rem; font-weight: 600; }
.user-message p, .answer-text { white-space: pre-wrap; line-height: 1.7; margin-bottom: 0; }
.assistant-message { position: relative; min-width: 0; width: fit-content; max-width: min(calc(100% - 2.5rem), 46rem); border: 1px solid var(--border); border-radius: .3rem 1.1rem 1.1rem 1.1rem; background: var(--surface-raised); padding: .8rem 1rem; }
.assistant-message > .answer-text { margin-top: .55rem; }
.expert-finding { margin-top: 1.1rem; }
.expert-finding h3, .answer-limitations h3 { font-size: .8rem; color: var(--muted-strong); margin: 0; }
.expert-finding p { line-height: 1.65; margin: .35rem 0; white-space: pre-wrap; }
.expert-finding details, .answer-limitations { font-size: .8rem; color: var(--muted-strong); line-height: 1.6; }
.answer-limitations { border-inline-start: 2px solid var(--border); padding-inline-start: 1rem; margin-top: 1rem; }
.follow-ups { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }
.follow-ups button { color: var(--accent); border: 1px solid var(--border); border-radius: .5rem; background: transparent; padding: .5rem .7rem; text-align: start; font: inherit; font-size: .8rem; cursor: pointer; }
.chat-composer { flex-shrink: 0; margin: .5rem 0 0; border: 1px solid var(--border); border-radius: .8rem; padding: .45rem .55rem; display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "attachments attachments" "input toolbar" "status status"; align-items: end; gap: .3rem .45rem; background: var(--control-bg); }
.chat-composer.file-drag-active { border-color: var(--accent); background: var(--accent-soft); }
.file-drop-hint { grid-column: 1 / -1; margin: 0; padding: .45rem .6rem; border: 1px dashed var(--accent); border-radius: .5rem; color: var(--accent); font-size: .8rem; text-align: center; }
.chat-composer:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.chat-composer textarea:focus, .chat-composer textarea:focus-visible { outline: none; box-shadow: none; border: 0; }
.chat-composer textarea { grid-area: input; align-self: center; resize: none; width: 100%; min-width: 0; min-height: 2.5rem; max-height: 8rem; overflow-y: hidden; border: 0; background: transparent; box-shadow: none; padding: .5rem .35rem; font-size: .875rem; line-height: 1.45; }
.composer-toolbar, .composer-tools { display: flex; align-items: center; gap: .4rem; }
.composer-toolbar { grid-area: toolbar; justify-content: flex-end; }
.composer-toolbar > button { display: inline-flex; align-items: center; justify-content: center; gap: .4rem; }
.composer-icon { display: inline-grid; flex: 0 0 var(--control-height); place-items: center; width: var(--control-height); height: var(--control-height); min-height: var(--control-height); padding: 0; border: 0; border-radius: .5rem; background: transparent; color: var(--accent); line-height: 1; box-shadow: none; }
.composer-icon:hover:not(:disabled) { border-color: transparent; background: var(--accent-soft); color: var(--accent); }
.composer-icon:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.composer-icon :deep(svg) { display: block; flex: none; }
.composer-icon.recording { color: var(--accent); background: var(--accent-soft); }
.composer-icon:disabled { background: var(--surface-soft); color: var(--muted-strong); cursor: not-allowed; }
.composer-status { grid-area: status; margin: 0; color: var(--muted-strong); font-size: .8rem; }
.composer-message { margin: .5rem 0; }
.draft-attachments, .sent-attachments { display: flex; flex-wrap: wrap; gap: .5rem; list-style: none; margin: 0; padding: 0; font-size: .8rem; }
.draft-attachments { grid-area: attachments; min-width: 0; max-height: 6rem; overflow-y: auto; overscroll-behavior: contain; padding: .15rem; }
.draft-attachments li, .sent-attachments li { display: flex; align-items: center; gap: .4rem; background: var(--bg); border: 1px solid var(--border); border-radius: .5rem; padding: .4rem .6rem; }
.draft-attachments li > span { overflow-wrap: anywhere; }
.draft-attachments button { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; border: 0; background: transparent; color: var(--muted-strong); cursor: pointer; padding: .2rem; line-height: 1; }
.draft-attachments button svg { display: block; flex: none; }
.sent-attachments { margin-top: .8rem; }
.conversation-footer { flex-shrink: 0; display: flex; justify-content: flex-end; align-items: start; gap: 1rem; padding: .55rem .15rem 0; color: var(--muted-strong); font-size: .75rem; line-height: 1.5; }
.conversation-footer button { background: transparent; color: var(--muted-strong); font: inherit; border: 0; text-decoration: underline; flex-shrink: 0; cursor: pointer; }
@media(max-width: 880px) {
  .expert-conversation { height: calc(100dvh - 12rem); }
}
@media(max-width: 640px) {
  .expert-entry { grid-template-columns: 1fr; }
  .entry-card { padding: 1.4rem; }
  .page-heading { flex-direction: row; flex-wrap: nowrap; align-items: flex-start; }
  .page-heading > div { flex: 1; min-width: 0; }
  .page-heading > .expert-settings-link, .conversation-new { flex: 0 0 var(--control-height); width: var(--control-height); height: var(--control-height); min-height: var(--control-height); padding: 0; display: inline-flex; align-items: center; justify-content: center; }
  .expert-settings-link span, .conversation-new span { display: none; }
  .mobile-action-icon { display: block; }
  .conversation-header { padding: .25rem 0 .75rem; flex-wrap: wrap; }
  .expert-conversation { height: calc(100dvh - 13rem); padding: .65rem .6rem .5rem; }
  .expert-transcript { padding: .85rem 0; gap: 1.2rem; }
  .message-row { gap: .45rem; }
  .message-avatar { flex-basis: 1.65rem; width: 1.65rem; height: 1.65rem; }
  .user-message, .assistant-message { max-width: calc(100% - 2.1rem); }
  .assistant-message { padding: .75rem .85rem; }
  .chat-composer { grid-template-columns: minmax(0, 1fr); grid-template-areas: "attachments" "input" "toolbar" "status"; padding: .35rem; gap: .2rem; }
  .chat-composer textarea { font-size: 1rem; }
  .composer-toolbar { justify-content: space-between; }
  .composer-tools { gap: .15rem; }
  .composer-icon { flex-basis: 2.5rem; width: 2.5rem; height: 2.5rem; min-height: 2.5rem; }
  .composer-toolbar > .button { padding-inline: .65rem; }
  .conversation-footer { padding-top: .45rem; }
  .history-item > a { flex-direction: column; align-items: flex-start; justify-content: center; gap: .15rem; }
  .history-item time { align-self: flex-start; text-align: start; }
}
@media(forced-colors: active) {
  .expert-transcript { scrollbar-color: auto; }
  .expert-transcript::-webkit-scrollbar-thumb, .chat-composer textarea::-webkit-scrollbar-thumb { background: ButtonText; }
  .expert-transcript:focus-visible { outline-color: Highlight; }
}
</style>
