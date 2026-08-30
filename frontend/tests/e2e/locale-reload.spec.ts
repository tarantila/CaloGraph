import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

const username = process.env.E2E_USERNAME ?? 'e2e-admin'
const password = process.env.E2E_PASSWORD ?? 'e2e-password-change-me'


async function login(page: Page) {
  await page.goto('/login')
  await page.getByRole('button', { name: /^(Sign in with password|Mit Passwort anmelden)$/ }).click()
  await expect(page.locator('input[autocomplete="username"]')).toBeVisible()
  await page.locator('input[autocomplete="username"]').fill(username)
  await page.locator('input[autocomplete="current-password"]').fill(password)
  await page.getByRole('button', { name: /^(Sign in|Anmelden)$/ }).click()
  await expect(page).toHaveURL(/\/(einrichtung)?$/)
  if (page.url().endsWith('/einrichtung')) {
    await page.locator('input[name="calories-kcal"]').fill('2100')
    await page.locator('input[name="protein-g"]').fill('135')
    await page.locator('form.setup-form button[type="submit"]').click()
  }
  await expect(page).toHaveURL(/\/$/)
}

async function setLocale(page: Page, locale: 'de' | 'en') {
  await page.goto('/konto/allgemeine-einstellungen')
  await page.locator('select[name="language"]').selectOption(locale)
  await page.locator('form').filter({ has: page.locator('select[name="language"]') }).getByRole('button').click()
  await expect(page.locator('html')).toHaveAttribute('lang', locale)
}

async function assertReloadLocale(
  page: Page,
  locale: 'de' | 'en',
  paths: Array<{ path: string; heading: RegExp }>,
) {
  for (const { path, heading } of paths) {
    await page.goto(path)
    await page.reload({ waitUntil: 'networkidle' })
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.getByRole('heading', { name: heading }).first()).toBeVisible()
  }
}

test('German profile stays German across protected full reloads', async ({ page }) => {
  await login(page)
  await setLocale(page, 'de')
  await assertReloadLocale(page, 'de', [
    { path: '/', heading: /^(Ernährungsüberblick|Übersicht)$/ },
    { path: '/konto/allgemeine-einstellungen', heading: /^Allgemeine Einstellungen$/ },
    { path: '/trends', heading: /^Trends$/ },
    { path: '/admin', heading: /^(Übersicht|Administrationsübersicht)$/ },
    { path: '/admin/security', heading: /^Anmeldeprotokoll$/ },
  ])
})

test('English profile stays English across protected full reloads', async ({ page }) => {
  await login(page)
  await setLocale(page, 'en')
  await assertReloadLocale(page, 'en', [
    { path: '/', heading: /^(Nutrition overview|Overview)$/ },
    { path: '/konto/allgemeine-einstellungen', heading: /^General settings$/ },
    { path: '/trends', heading: /^Trends$/ },
    { path: '/admin', heading: /^Overview$/ },
    { path: '/admin/security', heading: /^Sign-in log$/ },
  ])
})
