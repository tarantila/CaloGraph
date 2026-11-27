import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDictation } from '../src/composables/useDictation'

interface ResultEvent { resultIndex: number; results: Array<{ isFinal: boolean; 0: { transcript: string } }> }
class FakeRecognition {
  static instances: FakeRecognition[] = []
  lang = ''
  continuous = false
  interimResults = false
  onresult: ((event: ResultEvent) => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  abort = vi.fn()
  constructor() { FakeRecognition.instances.push(this) }
}

function makeHarness(update = vi.fn()) {
  const Harness = defineComponent({
    setup() { return useDictation(update) },
    template: '<div />',
  })
  return { wrapper: mount(Harness), update }
}

afterEach(() => {
  FakeRecognition.instances = []
  vi.unstubAllGlobals()
})

describe('useDictation', () => {
  it('requires a secure browser speech implementation and reports unsupported use', () => {
    vi.stubGlobal('isSecureContext', false)
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const { wrapper } = makeHarness()
    expect(wrapper.vm.available).toBe(false)
    wrapper.vm.start('', 'en')
    expect(wrapper.vm.failure).toBe('unsupported')
    expect(FakeRecognition.instances).toHaveLength(0)
    wrapper.unmount()
  })

  it('starts in the selected language, appends recognized words, and aborts on stop', () => {
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const { wrapper, update } = makeHarness()
    wrapper.vm.start('Hello', 'de')
    const recognition = FakeRecognition.instances[0]!
    expect(recognition.lang).toBe('de-DE')
    expect(recognition.start).toHaveBeenCalledOnce()
    expect(wrapper.vm.recording).toBe(true)
    recognition.onresult?.({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: 'Welt' } }] })
    expect(update).toHaveBeenCalledWith('Hello Welt')
    wrapper.vm.stop()
    expect(recognition.abort).toHaveBeenCalledOnce()
    expect(wrapper.vm.recording).toBe(false)
    wrapper.unmount()
  })

  it('ignores late recognition events after stop', () => {
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const { wrapper, update } = makeHarness()
    wrapper.vm.start('', 'en')
    const recognition = FakeRecognition.instances[0]!
    const lateResult = recognition.onresult
    wrapper.vm.stop()
    lateResult?.({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: 'late text' } }] })
    expect(update).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
