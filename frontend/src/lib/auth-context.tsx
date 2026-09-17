'use client'

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, type User } from './api'

interface AuthState {
  user: User | null
  loading: boolean
  error: string
  signIn(username: string, password: string): Promise<void>
  signOut(): Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api.me().then(
      result => { if (!cancelled) setUser(result) },
      () => { if (!cancelled) setUser(null) },
    ).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  async function signIn(username: string, password: string) {
    setError('')
    try { setUser(await api.login(username, password)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign in failed') }
  }

  async function signOut() {
    await api.logout()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, error, signIn, signOut }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
