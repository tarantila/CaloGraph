import { afterEach, describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError, localizeApiError } from '../src/api'

import { formatGermanDayMonth, formatGermanDate, parseGermanDate } from '../src/date-format'
import {
  DEFAULT_LOCALE,
  i18n,
  setLocale,
} from '../src/i18n'

afterEach(() => {
  setLocale(DEFAULT_LOCALE)
})

describe('interface language', () => {
  it('switches translations, document language and localized dates together', () => {
    setLocale('en')

    expect(i18n.global.t('navigation.overview')).toBe('Overview')
    expect(document.documentElement.lang).toBe('en')
    expect(formatGermanDate('2026-08-11')).toBe('11/08/2026')
    expect(formatGermanDayMonth('2026-07-03')).toBe('03/07')
    expect(parseGermanDate('07/03/2026')).toBe('2026-03-07')
  })

  it('falls back to German for unsupported locale values', () => {
    expect(setLocale('fr')).toBe('de')
    expect(i18n.global.t('navigation.overview')).toBe('Übersicht')
    expect(document.documentElement.lang).toBe('de')
  })

  it('localizes stable API problem types while preserving contextual details when requested', () => {
    setLocale('en')
    const error = new ApiError(
      'Password policy detail',
      422,
      undefined,
      undefined,
      'urn:calograph:problem:admin-reauthentication-failed',
    )

    expect(localizeApiError(error)).toBe('Administrator reauthentication failed.')
    setLocale('de')
    expect(localizeApiError(new ApiError('Session expired', 401, undefined, undefined, 'urn:calograph:problem:session-expired'))).toBe('Sitzung ist abgelaufen.')
    setLocale('en')
    const policyError = new ApiError(
      'Specific password policy detail',
      422,
      undefined,
      undefined,
      'urn:calograph:problem:validation-error',
    )
    expect(localizeApiError(policyError, 'auth.registrationFailed', {
      problemTypeFallbacks: { 'urn:calograph:problem:validation-error': 'auth.passwordPolicy' },
      preserveDetail: true,
      preserveDetailForProblemTypes: ['urn:calograph:problem:validation-error'],
    })).toBe('Specific password policy detail')
    expect(localizeApiError(new ApiError('Das Passwort erfüllt die Regeln nicht.', 422, undefined, undefined, 'urn:calograph:problem:validation-error'), 'auth.registrationFailed', {
      problemTypeFallbacks: { 'urn:calograph:problem:validation-error': 'auth.passwordPolicy' },
    })).toBe('The password does not meet the security requirements. Use an unusual long passphrase or a password manager.')
    expect(localizeApiError(new ApiError('Recovery-token detail', 400), 'errors.generic', { preserveDetail: true })).toBe('Recovery-token detail')
    expect(localizeApiError(new ApiError('Legacy detail', 400))).toBe('Legacy detail')
  })
  it('lokalisiert Transportfehler ohne native Browsermeldung', () => {
    setLocale('de')
    expect(localizeApiError(new ApiTransportError())).toBe(
      'Die Verbindung zu CaloGraph ist fehlgeschlagen. Bitte versuche es erneut.',
    )
    setLocale('en')
    expect(localizeApiError(new ApiTransportError())).toBe(
      'Could not connect to CaloGraph. Please try again.',
    )
  })
  it('does not derive the locale from browser or public storage state', () => {
    localStorage.setItem('calograph_language', 'de')
    setLocale('en')

    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(localStorage.getItem('calograph_language')).toBe('de')
    localStorage.removeItem('calograph_language')
  })
})
