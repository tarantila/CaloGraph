import { expect, test } from '@playwright/test'

const baselineHeaders = {
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
  'referrer-policy': 'no-referrer',
  'permissions-policy': 'camera=(), microphone=(), geolocation=()',
  'cross-origin-resource-policy': 'same-origin',
  'x-permitted-cross-domain-policies': 'none',
}

test('serves consistent security headers for documents and static assets', async ({ request }) => {
  const documentResponse = await request.get('/')
  const documentHeaders = documentResponse.headers()

  expect(documentResponse.ok()).toBe(true)
  expect(documentHeaders).toMatchObject(baselineHeaders)
  expect(documentHeaders['cache-control']).toBe('private, no-store')
  expect(documentHeaders['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(documentHeaders['cross-origin-opener-policy']).toBeUndefined()
  expect(documentHeaders['strict-transport-security']).toBeUndefined()

  const trustedLocalResponse = await request.get('/', {
    headers: { Host: 'localhost' },
  })
  expect(trustedLocalResponse.headers()['cross-origin-opener-policy']).toBe('same-origin')

  const document = await documentResponse.text()
  const scriptPath = document.match(/<script[^>]+src="([^"]+)"/)?.[1]
  expect(scriptPath).toBeTruthy()

  const assetResponse = await request.get(scriptPath!)
  const assetHeaders = assetResponse.headers()

  expect(assetResponse.ok()).toBe(true)
  expect(assetHeaders).toMatchObject(baselineHeaders)
  expect(assetHeaders['cache-control']).toBe('public, max-age=604800, immutable')

  const apiResponse = await request.get('/api/v1/auth/me', {
    headers: {
      Host: 'localhost',
      'X-Request-ID': 'attacker-controlled',
    },
  })
  const apiHeaders = apiResponse.headers()

  expect(apiResponse.status()).toBe(401)
  expect(apiHeaders).toMatchObject(baselineHeaders)
  expect(apiHeaders['cache-control']).toBe('private, no-store')
  expect(apiHeaders['x-request-id']).toMatch(/^[a-f0-9]{32}$/)
})

test('ignores forwarded HTTPS from an untrusted peer', async ({ request }) => {
  const response = await request.get('/', {
    headers: { 'X-Forwarded-Proto': 'https' },
  })

  expect(response.headers()['strict-transport-security']).toBeUndefined()
  expect(response.headers()['cross-origin-opener-policy']).toBeUndefined()
})
