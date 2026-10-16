import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { h, nextTick } from 'vue'

import PeriodPicker from '../src/components/PeriodPicker.vue'
import { formatGermanDate } from '../src/date-format'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

const wrappers: VueWrapper[] = []

beforeEach(() => setLocale('de'))
afterEach(() => {
  for (const wrapper of wrappers.splice(0)) {
    const element = wrapper.element
    wrapper.unmount()
    element.remove()
  }
  setLocale(DEFAULT_LOCALE)
})

function picker(maxDays = 31) {
  const wrapper = mount(PeriodPicker, {
    attachTo: document.body,
    props: {
      start: '2024-03-01', end: '2024-03-30', today: '2024-03-30', period: '30',
      minDays: 1, maxDays, customRange: true, maxDate: '2024-03-30',
      periodCapabilities: { supports_all_data: false, all_data_start_date: null },
      allRange: null,
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

async function openPicker(wrapper: VueWrapper, mobile = false) {
  const opener = wrapper.get<HTMLButtonElement>(mobile
    ? '.analysis-period-trigger'
    : '.analysis-period-custom-button')
  opener.element.focus()
  await opener.trigger('click')
  await nextTick()
  return opener
}

function dateFields(wrapper: VueWrapper) {
  const fields = wrapper.findAll<HTMLInputElement>('input[type="text"]')
  return { start: fields[0]!, end: fields[1]! }
}

describe('PeriodPicker reader boundaries', () => {
  it('keeps all three desktop slots and explains disabled 90 while omitting it from the popover', async () => {
    const wrapper = picker()
    expect(wrapper.findAll('.analysis-period-strip [data-period]').map((button) => button.attributes('data-period')))
      .toEqual(['7', '30', '90'])
    const ninety = wrapper.get<HTMLButtonElement>('.analysis-period-strip [data-period="90"]')
    expect(ninety.element.disabled).toBe(true)
    const descriptionId = ninety.attributes('aria-describedby')
    expect(descriptionId).toBeTruthy()
    const description = document.getElementById(descriptionId!)
    expect(description).not.toBeNull()
    expect(wrapper.element.contains(description)).toBe(true)
    expect(description!.textContent).toContain('31')
    expect(wrapper.get('.analysis-period-reader-limit').isVisible()).toBe(true)
    await ninety.trigger('click')
    expect(wrapper.emitted('apply')).toBeUndefined()
    await openPicker(wrapper)
    expect(wrapper.findAll('.analysis-period-option').map((option) => option.attributes('data-period')))
      .toEqual(['7', '30'])
  })

  it('applies a supported desktop preset immediately without changing the controlled selection itself', async () => {
    const wrapper = picker()
    await wrapper.get('.analysis-period-strip [data-period="7"]').trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-03-24', end: '2024-03-30', period: '7' },
    ]])
    expect(wrapper.get('.analysis-period-strip [data-period="30"]').attributes('aria-pressed')).toBe('true')
    await wrapper.setProps({ start: '2024-03-24', period: '7' })
    expect(wrapper.get('.analysis-period-strip [data-period="7"]').attributes('aria-pressed')).toBe('true')
  })

  it('omits unsupported mobile choices and permits only quick presets when custom ranges are forbidden', async () => {
    const wrapper = picker()
    await wrapper.setProps({ customRange: false, minDays: 8 })
    const custom = wrapper.get<HTMLButtonElement>('.analysis-period-custom-button')
    expect(custom.element.disabled).toBe(true)
    expect(document.getElementById(custom.attributes('aria-describedby')!)).not.toBeNull()
    await custom.trigger('click')
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
    await openPicker(wrapper, true)
    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
    expect(wrapper.findAll('.analysis-period-option').map((option) => option.attributes('data-period')))
      .toEqual(['30'])
    const thirty = wrapper.get<HTMLButtonElement>('.analysis-period-option[data-period="30"]')
    expect(document.activeElement).toBe(thirty.element)
    await thirty.trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-03-01', end: '2024-03-30', period: '30' },
    ]])
  })

  it('does not apply a text draft until explicit submit, then emits its exact inclusive dates once', async () => {
    const wrapper = picker()
    const opener = await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.start.setValue('29.02.2024')
    await fields.end.setValue('30.03.2024')
    await fields.start.trigger('blur')
    expect(wrapper.emitted('apply')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-02-29', end: '2024-03-30', period: 'custom' },
    ]])
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
    expect(document.activeElement).toBe(opener.element)
  })


  it.each(['start', 'end'] as const)('rejects invalid %s text instead of submitting the previous valid draft', async (field) => {
    const wrapper = picker()
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.start.setValue('29.02.2024')
    await fields.end.setValue('30.03.2024')
    await fields[field].setValue('31.02.2024')
    expect(fields[field].element.validity.valid).toBe(false)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').isVisible()).toBe(true)
    await fields[field].setValue(field === 'start' ? '29.02.2024' : '30.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-02-29', end: '2024-03-30', period: 'custom' },
    ]])
  })

  it('accepts a native calendar draft only after explicit submit', async () => {
    const wrapper = picker()
    await openPicker(wrapper)
    await wrapper.findAll('input[type="date"]')[0]!.setValue('2024-02-29')
    expect(dateFields(wrapper).start.element.value).toBe('29.02.2024')
    expect(wrapper.emitted('apply')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-02-29', end: '2024-03-30', period: 'custom' },
    ]])
  })

  it.each([
    { maxDays: 31, exactStart: '2024-02-29', tooLongStart: '2024-02-28' },
    { maxDays: 365, exactStart: '2023-04-01', tooLongStart: '2023-03-31' },
    { maxDays: 3661, exactStart: '2014-03-23', tooLongStart: '2014-03-22' },
  ])('rejects one day beyond $maxDays but accepts the exact inclusive boundary', async ({ maxDays, exactStart, tooLongStart }) => {
    const wrapper = picker(maxDays)
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.start.setValue(formatGermanDate(tooLongStart))
    await fields.end.setValue('30.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').isVisible()).toBe(true)
    await fields.start.setValue(formatGermanDate(exactStart))
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: exactStart, end: '2024-03-30', period: 'custom' },
    ]])
  })

  it('keeps the compact Analysis quick bar and exact 365-day popover choices', async () => {
    const wrapper = picker(365)
    expect(wrapper.findAll('.analysis-period-strip [data-period]').map((button) => button.text()))
      .toEqual(['7 Tage', '30 Tage', '90 Tage'])
    await openPicker(wrapper)
    expect(wrapper.findAll('.analysis-period-option').map((option) => option.text()))
      .toEqual(['7 Tage', '30 Tage', '90 Tage', '6 Monate', '1 Jahr'])
    expect(wrapper.find('[data-period="all"]').exists()).toBe(false)
  })

  it('rejects a 366-day draft without committing it and restores committed dates on reopen', async () => {
    const wrapper = picker(365)
    await wrapper.setProps({
      start: '2024-01-01', end: '2024-03-30', today: '2024-03-30', period: '90',
    })
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.start.setValue('31.03.2023')
    await fields.end.setValue('30.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    expect(wrapper.get('[role="alert"]').text()).toContain('365')
    expect(wrapper.props()).toMatchObject({
      start: '2024-01-01', end: '2024-03-30', today: '2024-03-30', period: '90',
    })
    await fields.start.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
    await openPicker(wrapper)
    expect(dateFields(wrapper).start.element.value).toBe('01.01.2024')
    expect(dateFields(wrapper).end.element.value).toBe('30.03.2024')
    expect(wrapper.find('.analysis-period-error').exists()).toBe(false)
  })

  it('rejects reversed dates and a shorter-than-minimum range, then accepts the minimum', async () => {
    const wrapper = picker()
    await wrapper.setProps({ minDays: 2 })
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.start.setValue('30.03.2024')
    await fields.end.setValue('29.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    await fields.end.setValue('30.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    await fields.start.setValue('29.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-03-29', end: '2024-03-30', period: 'custom' },
    ]])
  })

  it('enforces maxDate when given but allows a future range when the reader has no such limit', async () => {
    const wrapper = picker()
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    await fields.end.setValue('31.03.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toBeUndefined()
    expect(fields.end.element.validity.valid).toBe(false)
    await wrapper.setProps({ maxDate: undefined })
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-03-01', end: '2024-03-31', period: 'custom' },
    ]])
  })

  it.each([
    { period: '180' as const, start: '2023-10-03' },
    { period: '365' as const, start: '2023-04-01' },
  ])('applies $period rolling inclusive days immediately from the popover', async ({ period, start }) => {
    const wrapper = picker(365)
    const opener = await openPicker(wrapper)
    await wrapper.get(`.analysis-period-option[data-period="${period}"]`).trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[
      { start, end: '2024-03-30', period },
    ]])
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
    expect(document.activeElement).toBe(opener.element)
  })
})

describe('PeriodPicker full reader range', () => {
  it('uses the exact all boundary and resolved end, then loses the option when capability disappears', async () => {
    const wrapper = picker()
    await wrapper.setProps({
      periodCapabilities: { supports_all_data: true, all_data_start_date: '2024-02-29' },
      allRange: { start: '2024-02-29', end: '2024-03-29' },
    })
    await openPicker(wrapper)
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    expect(wrapper.emitted('apply')).toEqual([[
      { start: '2024-02-29', end: '2024-03-29', period: 'all' },
    ]])
    await wrapper.setProps({ periodCapabilities: undefined })
    await openPicker(wrapper)
    expect(wrapper.find('[data-period="all"]').exists()).toBe(false)
  })

  it.each([
    { name: 'missing capability', capability: undefined, range: { start: '2024-02-29', end: '2024-03-30' } },
    { name: 'null capability', capability: null, range: { start: '2024-02-29', end: '2024-03-30' } },
    { name: 'false capability with a plausible boundary', capability: { supports_all_data: false, all_data_start_date: '2024-02-29' }, range: { start: '2024-02-29', end: '2024-03-30' } },
    { name: 'true without a boundary', capability: { supports_all_data: true, all_data_start_date: null }, range: { start: '2024-02-29', end: '2024-03-30' } },
    { name: 'missing full range', capability: { supports_all_data: true, all_data_start_date: '2024-02-29' }, range: null },
    { name: 'mismatched start', capability: { supports_all_data: true, all_data_start_date: '2024-02-28' }, range: { start: '2024-02-29', end: '2024-03-30' } },
    { name: '32 days beyond a 31-day reader', capability: { supports_all_data: true, all_data_start_date: '2024-02-28' }, range: { start: '2024-02-28', end: '2024-03-30' } },
    { name: 'invalid calendar start', capability: { supports_all_data: true, all_data_start_date: '2024-02-30' }, range: { start: '2024-02-30', end: '2024-03-30' } },
  ])('does not offer all for $name', async ({ capability, range }) => {
    const wrapper = picker()
    await wrapper.setProps({ periodCapabilities: capability, allRange: range })
    await openPicker(wrapper, true)
    expect(wrapper.find('[data-period="all"]').exists()).toBe(false)
  })

  it('removes all from an already open picker when support is revoked', async () => {
    const wrapper = picker()
    await wrapper.setProps({
      periodCapabilities: { supports_all_data: true, all_data_start_date: '2024-02-29' },
      allRange: { start: '2024-02-29', end: '2024-03-30' },
    })
    await openPicker(wrapper)
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(true)
    await wrapper.setProps({ periodCapabilities: { supports_all_data: false, all_data_start_date: null } })
    expect(wrapper.find('[data-period="all"]').exists()).toBe(false)
    expect(wrapper.emitted('apply')).toBeUndefined()
  })
})

describe('PeriodPicker focus and controlled drafts', () => {
  it('focuses the first supported quick option or the first date input for a custom selection', async () => {
    const wrapper = picker()
    const opener = await openPicker(wrapper)
    expect(document.activeElement).toBe(wrapper.get('.analysis-period-option[data-period="7"]').element)
    await opener.trigger('click')
    await wrapper.setProps({ period: 'custom' })
    await openPicker(wrapper)
    expect(document.activeElement).toBe(dateFields(wrapper).start.element)
  })

  it('focuses free date entry when no quick preset satisfies the minimum', async () => {
    const wrapper = picker()
    await wrapper.setProps({ minDays: 31 })
    await openPicker(wrapper)
    expect(wrapper.find('.analysis-period-option').exists()).toBe(false)
    expect(document.activeElement).toBe(dateFields(wrapper).start.element)
  })

  it('discards a draft on Escape and restores the correct opener with unique IDs across two instances', async () => {
    const props = {
      start: '2024-03-01', end: '2024-03-30', today: '2024-03-30', period: '30' as const,
      minDays: 1, maxDays: 31, customRange: true, maxDate: '2024-03-30',
      periodCapabilities: { supports_all_data: false, all_data_start_date: null },
      allRange: null,
    }
    const host = mount({
      render: () => h('div', [h(PeriodPicker, props), h(PeriodPicker, props)]),
    }, { attachTo: document.body })
    wrappers.push(host)
    const instances = host.findAllComponents(PeriodPicker)
    const first = instances[0]!
    const second = instances[1]!
    const firstOpener = await openPicker(first)
    const firstId = first.get('.analysis-period-popover').attributes('id')
    expect(firstOpener.attributes('aria-controls')).toBe(firstId)
    expect(first.get('.analysis-period-trigger').attributes('aria-controls')).toBe(firstId)
    const secondOpener = await openPicker(second)
    const secondId = second.get('.analysis-period-popover').attributes('id')
    expect(secondOpener.attributes('aria-controls')).toBe(secondId)
    expect(second.get('.analysis-period-trigger').attributes('aria-controls')).toBe(secondId)
    expect(secondId).not.toBe(firstId)
    expect(second.get('.analysis-period-strip [data-period="90"]').attributes('aria-describedby'))
      .not.toBe(first.get('.analysis-period-strip [data-period="90"]').attributes('aria-describedby'))
    await dateFields(second).start.setValue('29.02.2024')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(secondOpener.element)
    expect(second.find('.analysis-period-popover').exists()).toBe(false)
    expect(second.emitted('apply')).toBeUndefined()
    await openPicker(second)
    expect(dateFields(second).start.element.value).toBe('01.03.2024')
  })

  it('restores the mobile opener after Escape without applying its draft', async () => {
    const wrapper = picker()
    const opener = await openPicker(wrapper, true)
    await dateFields(wrapper).start.setValue('29.02.2024')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(opener.element)
    expect(opener.attributes('aria-expanded')).toBe('false')
    expect(wrapper.emitted('apply')).toBeUndefined()
  })

  it('keeps focus moves inside open, ignores null focusout, and closes on Tab-style focus leaving the root', async () => {
    const wrapper = picker()
    await openPicker(wrapper)
    const fields = dateFields(wrapper)
    fields.start.element.focus()
    await nextTick()
    fields.end.element.focus()
    await nextTick()
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(true)
    await fields.end.trigger('focusout', { relatedTarget: null })
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(true)
    const outside = document.createElement('button')
    document.body.append(outside)
    try {
      outside.focus()
      await nextTick()
      expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
      expect(document.activeElement).toBe(outside)
      expect(wrapper.emitted('apply')).toBeUndefined()
    } finally {
      outside.remove()
    }
  })

  it('discards an outside-click draft and reloads the latest controlled dates on reopening', async () => {
    const wrapper = picker()
    await openPicker(wrapper)
    await dateFields(wrapper).start.setValue('29.02.2024')
    document.body.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    await nextTick()
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(false)
    expect(wrapper.emitted('apply')).toBeUndefined()
    await wrapper.setProps({ start: '2024-03-24', period: '7' })
    await openPicker(wrapper)
    expect(dateFields(wrapper).start.element.value).toBe('24.03.2024')
    expect(dateFields(wrapper).end.element.value).toBe('30.03.2024')
  })

  it('keeps desktop custom selection compact while the mobile trigger retains both date boundaries', async () => {
    const wrapper = picker(3661)
    await wrapper.setProps({ start: '2014-03-23', period: 'custom' })
    const mobile = wrapper.get('.analysis-period-trigger')
    const desktop = wrapper.get('.analysis-period-custom-button')
    expect(mobile.text()).toContain('Individuell')
    expect(mobile.text()).toContain('23.03.2014')
    expect(mobile.text()).toContain('30.03.2024')
    expect(mobile.attributes('aria-label')).toContain('23.03.2014')
    expect(mobile.attributes('aria-label')).toContain('30.03.2024')
    expect(desktop.text().trim()).toBe('Individuell')
    expect(desktop.attributes('aria-pressed')).toBe('true')
    await desktop.trigger('click')
    expect(wrapper.find('.analysis-period-popover').exists()).toBe(true)
  })
})
