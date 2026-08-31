import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import DateInput from '../src/components/DateInput.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

beforeEach(() => {
  setLocale(DEFAULT_LOCALE)
})

afterEach(() => {
  setLocale(DEFAULT_LOCALE)
})
describe('DateInput', () => {
  it('shows German dates and emits ISO values for valid manual input', async () => {
    const wrapper = mount(DateInput, { props: { modelValue: '2026-08-11' } })
    const textInput = wrapper.get<HTMLInputElement>('input[type="text"]')

    expect(textInput.element.value).toBe('11.08.2026')
    await textInput.setValue('03.07.2026')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['2026-07-03'])
    expect(textInput.element.validationMessage).toBe('')
  })

  it('renders the calendar trigger as a compact icon control', () => {
    const wrapper = mount(DateInput, { props: { modelValue: '2026-08-11' } })
    const button = wrapper.get('.date-input-picker-button')

    expect(button.text()).toBe('')
    expect(button.get('svg').attributes('aria-hidden')).toBe('true')
    expect(button.attributes('aria-label')).toBe('Kalender öffnen')
  })
  it('reformats the current value when the locale changes', async () => {
    const wrapper = mount(DateInput, { props: { modelValue: '2026-08-11' } })

    setLocale('en')
    await nextTick()

    expect(wrapper.get<HTMLInputElement>('input[type="text"]').element.value).toBe('11/08/2026')
  })

  it('rejects impossible manual dates without emitting a changed value', async () => {
    const wrapper = mount(DateInput, { props: { modelValue: '2026-08-11' } })
    const textInput = wrapper.get<HTMLInputElement>('input[type="text"]')

    await textInput.setValue('31.02.2026')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(textInput.element.validationMessage).toContain('TT.MM.JJJJ')
  })

  it('keeps max validation after the parent accepts a manual future date', async () => {
    const wrapper = mount(DateInput, {
      props: { modelValue: '2026-08-11', max: '2026-12-30' },
    })
    const textInput = wrapper.get<HTMLInputElement>('input[type="text"]')

    await textInput.setValue('31.12.2026')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['2026-12-31'])
    await wrapper.setProps({ modelValue: '2026-12-31' })
    await nextTick()

    expect(textInput.element.validationMessage).toBe('Das Datum darf nicht in der Zukunft liegen.')
    expect(wrapper.vm.reportValidity()).toBe(false)

    await wrapper.setProps({ modelValue: '2026-12-30' })
    await nextTick()

    expect(textInput.element.validationMessage).toBe('')
    expect(wrapper.vm.reportValidity()).toBe(true)

    await wrapper.setProps({ max: '2026-12-29' })
    await nextTick()

    expect(textInput.element.validationMessage).toBe('Das Datum darf nicht in der Zukunft liegen.')
    expect(wrapper.vm.reportValidity()).toBe(false)
  })

  it('keeps the native calendar picker as an ISO boundary', async () => {
    const wrapper = mount(DateInput, { props: { modelValue: '2026-08-11' } })

    await wrapper.get('input[type="date"]').setValue('2024-02-29')

    expect(wrapper.get<HTMLInputElement>('input[type="text"]').element.value).toBe('29.02.2024')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['2024-02-29'])
  })
})
