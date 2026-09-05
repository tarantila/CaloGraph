import { afterEach, describe, expect, it } from 'vitest'

import { ApiError, ApiTransportError, localizeApiError } from '../src/api'

import { formatGermanDayMonth, formatGermanDate, parseGermanDate } from '../src/date-format'
import {
  DEFAULT_LOCALE,
  createKcalFormatter,
  i18n,
  roundHalfUp,
  setLocale,
} from '../src/i18n'

afterEach(() => {
  setLocale(DEFAULT_LOCALE)
})

describe('calorie presentation rounding', () => {
  it('uses half-up boundaries without changing the input value', () => {
    expect(roundHalfUp(1204.49)).toBe(1204)
    expect(roundHalfUp(1204.5)).toBe(1205)
    expect(roundHalfUp(1204.51)).toBe(1205)
    expect(roundHalfUp(1528.8668)).toBe(1529)
  })

  it('formats calories as whole kcal in the active locale', () => {
    setLocale('de')
    const formatter = createKcalFormatter()
    expect(formatter.format(1204.5)).toBe('1.205')
    expect(formatter.format(1528.8668)).toBe('1.529')
  })
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
  it('keeps account unit system translations available in both locales', () => {
    expect(i18n.global.te('accountGeneral.unitSystem', 'de')).toBe(true)
    expect(i18n.global.te('accountGeneral.unitSystem', 'en')).toBe(true)
    expect(i18n.global.te('accountGeneral.unitSystemOptions.metric', 'de')).toBe(true)
    expect(i18n.global.te('accountGeneral.unitSystemOptions.metric', 'en')).toBe(true)
    expect(i18n.global.te('accountPersonal.heightImperial', 'de')).toBe(true)
    expect(i18n.global.te('accountPersonal.heightImperial', 'en')).toBe(true)
  })
  it('keeps target weight mode labels consistent in German and English', () => {
    expect(i18n.global.t('settingsUi.targetWeightNone', {}, { locale: 'de' })).toBe('Kein Zielgewicht')
    expect(i18n.global.t('settingsUi.targetWeightExact', {}, { locale: 'de' })).toBe('Festes Zielgewicht')
    expect(i18n.global.t('settingsUi.targetWeightRange', {}, { locale: 'de' })).toBe('Zielbereich')
    expect(i18n.global.t('settingsUi.targetWeightNone', {}, { locale: 'en' })).toBe('No target weight')
    expect(i18n.global.t('settingsUi.targetWeightExact', {}, { locale: 'en' })).toBe('Fixed target weight')
    expect(i18n.global.t('settingsUi.targetWeightRange', {}, { locale: 'en' })).toBe('Target range')
  })


  it('provides data export controls in German and English', () => {
    expect(i18n.global.t('accountData.dataExportTitle')).toBe('Datenexport')
    expect(i18n.global.t('accountData.dataExportAction')).toBe('Export herunterladen')

    setLocale('en')
    expect(i18n.global.t('accountData.dataExportTitle')).toBe('Data export')
    expect(i18n.global.t('accountData.dataExportAction')).toBe('Download export')
  })

  it('localizes the compact custom period label', () => {
    expect(i18n.global.t('dateFilter.individual')).toBe('Individuell')
    setLocale('en')
    expect(i18n.global.t('dateFilter.individual')).toBe('Custom')
  })

  it('localizes the admin center, status pages, and portable data areas', () => {
    expect(i18n.global.t('adminNav.title')).toBe('Admin-Center')
    expect(i18n.global.t('adminNav.overviewTitle')).toBe('Übersicht')
    expect(i18n.global.t('adminNav.systemTitle')).toBe('Systemstatus')
    expect(i18n.global.t('accountData.portableImportTitle')).toBe('CaloGraph-Datensicherung importieren')
    setLocale('en')
    expect(i18n.global.t('adminNav.title')).toBe('Admin Center')
    expect(i18n.global.t('adminNav.overviewTitle')).toBe('Overview')
    expect(i18n.global.t('adminNav.systemTitle')).toBe('System Status')
    expect(i18n.global.t('accountData.portableImportTitle')).toBe('Import CaloGraph backup')
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
    expect(localizeApiError(new ApiError(
      'Ein anderer Datenexport läuft bereits. Bitte versuche es in Kürze erneut.',
      429,
      undefined,
      '30',
      'urn:calograph:problem:data-export-busy',
    ))).toBe('Another data export is already running. Please try again shortly.')
    setLocale('de')
    expect(localizeApiError(new ApiError(
      'Ein anderer Datenexport läuft bereits. Bitte versuche es in Kürze erneut.',
      429,
      undefined,
      '30',
      'urn:calograph:problem:data-export-busy',
    ))).toBe('Ein anderer Datenexport läuft bereits. Bitte versuche es in Kürze erneut.')
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
