import { afterEach, vi } from 'vitest'

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

