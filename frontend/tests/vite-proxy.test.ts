import { describe, expect, it } from 'vitest'

import viteConfig from '../vite.config'

describe('Vite development API proxy', () => {
  it('preserves the browser host for backend TrustedHost validation', () => {
    const proxy = viteConfig.server?.proxy as Record<string, {
      target?: string
      changeOrigin?: boolean
      headers?: Record<string, string>
    }>

    expect(proxy['/api']).toMatchObject({
      target: 'http://backend:8000',
      changeOrigin: false,
      headers: {
        'X-Real-IP': '127.0.0.1',
        'X-Forwarded-For': '127.0.0.1',
        'X-Forwarded-Proto': 'http',
        'X-Forwarded-Host': '127.0.0.1:8180',
      },
    })
  })
})
