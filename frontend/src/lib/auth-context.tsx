'use client'

import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, onUnauthorized, type User } from './api'

interface AuthState {
  user: User | null
  loading: boolean
  error: string
  signIn(username: string, password: string): Promise<void>
  signOut(): Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export const SESSION_EXPIRED = 'Your session has expired. Sign in again.'

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const signedIn = useRef(false)

  function endSession(message = '') {
    signedIn.current = false
    setUser(null)
    setError(message)
    // Nothing fetched for the old session should show, or flash its error, after the next sign-in.
    client.removeQueries()
  }

  useEffect(() => {
    let cancelled = false
    api.me().then(
      result => { if (!cancelled) { signedIn.current = true; setUser(result) } },
      () => { if (!cancelled) setUser(null) },
    ).finally(() => { if (!cancelled) setLoading(false) })
    // A 401 on any request while signed in means the session ended: go back to sign-in once, not one error per panel.
    const unsubscribe = onUnauthorized(() => { if (signedIn.current) endSession(SESSION_EXPIRED) })
    return () => { cancelled = true; unsubscribe() }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function signIn(username: string, password: string) {
    setError('')
    try {
      const result = await api.login(username, password)
      signedIn.current = true
      setUser(result)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Sign in failed') }
  }

  async function signOut() {
    try { await api.logout() }
    // A session that already ended on the server is as signed out as it gets.
    catch (reason) { if (!(reason instanceof ApiError && reason.status === 401)) throw reason }
    endSession()
  }

  return <AuthContext.Provider value={{ user, loading, error, signIn, signOut }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
