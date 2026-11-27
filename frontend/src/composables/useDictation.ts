import { computed, onBeforeUnmount, ref } from 'vue'

interface SpeechResult { isFinal: boolean; 0: { transcript: string } }
interface SpeechEvent { resultIndex: number; results: { length: number; [index: number]: SpeechResult } }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean
  onresult: ((event: SpeechEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void; abort(): void
}
type RecognitionConstructor = new () => Recognition
function constructor(): RecognitionConstructor | undefined {
  const surface = window as Window & { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  return surface.SpeechRecognition ?? surface.webkitSpeechRecognition
}

/** Browser-managed speech recognition; audio never enters CaloGraph's backend. */
export function useDictation(update: (text: string) => void) {
  const recording = ref(false)
  const failure = ref('')
  const available = computed(() => window.isSecureContext && Boolean(constructor()))
  let recognition: Recognition | null = null
  let epoch = 0
  function stop(): void {
    epoch++
    if (recognition) {
      recognition.onresult = null; recognition.onerror = null; recognition.onend = null
      try { recognition.abort() } catch { /* Recognition may already have ended. */ }
      recognition = null
    }
    recording.value = false
  }
  function start(initial: string, language: string): void {
    stop(); failure.value = ''
    const Factory = constructor()
    if (!available.value || !Factory) { failure.value = 'unsupported'; return }
    const token = epoch
    const instance = new Factory()
    recognition = instance
    instance.lang = language === 'de' ? 'de-DE' : 'en-US'
    instance.continuous = true; instance.interimResults = true
    instance.onresult = event => {
      if (token !== epoch) return
      let text = ''
      for (let index = 0; index < event.results.length; index++) text += event.results[index]![0].transcript + ' '
      update((initial.trimEnd() + (initial.trim() ? ' ' : '') + text.trim()).slice(0, 8000))
    }
    instance.onerror = event => {
      if (token !== epoch) return
      failure.value = ['not-allowed', 'service-not-allowed', 'audio-capture'].includes(event.error) ? 'permission' : 'unavailable'
      stop()
    }
    instance.onend = () => { if (token === epoch) stop() }
    try { instance.start(); recording.value = true }
    catch { failure.value = 'unavailable'; stop() }
  }
  onBeforeUnmount(stop)
  return { recording, available, failure, start, stop }
}
