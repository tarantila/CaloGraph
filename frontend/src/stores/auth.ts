import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, setCsrfToken } from '../api'
import type { User } from '../types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)

  async function ensureUser(): Promise<boolean> {
    if (user.value) return true
    try {
      user.value = await api<User>('/auth/me')
      return true
    } catch {
      return false
    }
  }

  async function login(username: string, password: string): Promise<void> {
    loading.value = true
    try {
      const result = await api<{ user: User; csrf_token: string }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      user.value = result.user
      setCsrfToken(result.csrf_token)
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    await api<void>('/auth/logout', { method: 'POST' })
    user.value = null
    setCsrfToken(null)
  }

  return { user, loading, ensureUser, login, logout }
})

