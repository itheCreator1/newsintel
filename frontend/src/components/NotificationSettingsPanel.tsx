'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { NotificationSettings, NotificationSettingsUpdate } from '../lib/api-types'
import { fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'
import { GlassPanel } from './GlassPanel'
import { LoadError } from './Feedback'

const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const hourLabel = (hour: number) => `${String(hour).padStart(2, '0')}:00`
// The API sends times as HH:MM:SS; a time field shows HH:MM.
const clock = (value: string | null) => value ? value.slice(0, 5) : ''

function timeZones(current: string): string[] {
  let zones: string[] = []
  try { zones = Intl.supportedValuesOf('timeZone') } catch { zones = [] }
  return [...new Set(['UTC', current, Intl.DateTimeFormat().resolvedOptions().timeZone, ...zones])].filter(Boolean)
}

interface Draft { enabled: boolean; server: string; topic: string; token: string; publicUrl: string; timezone: string; quiet: boolean; quietStart: string; quietEnd: string; digest: boolean; digestHour: number }

const draftFrom = (settings: NotificationSettings): Draft => ({
  enabled: settings.enabled, server: settings.server ?? '', topic: settings.topic ?? '', token: '', publicUrl: settings.public_url ?? '',
  timezone: settings.timezone, quiet: Boolean(settings.quiet_start && settings.quiet_end), quietStart: clock(settings.quiet_start) || '22:00', quietEnd: clock(settings.quiet_end) || '07:00',
  digest: settings.digest_enabled, digestHour: settings.digest_hour,
})

/** Where (an ntfy topic) and when (quiet hours, a daily summary) news from watched investigations is pushed. */
export function NotificationSettingsPanel() {
  const client = useQueryClient()
  const settings = useQuery({ queryKey: ['notification-settings'], queryFn: api.notificationSettings, retry: false })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [hasToken, setHasToken] = useState(false)
  // The form starts from the saved settings once; later refetches must not overwrite what is being typed.
  useEffect(() => { if (settings.data && !draft) { setDraft(draftFrom(settings.data)); setHasToken(settings.data.has_token) } }, [settings.data, draft])
  const zones = useMemo(() => timeZones(draft?.timezone ?? 'UTC'), [draft?.timezone])

  const payload = (current: Draft): NotificationSettingsUpdate => ({
    enabled: current.enabled, server: current.server.trim() || null, topic: current.topic.trim() || null, public_url: current.publicUrl.trim() || null,
    timezone: current.timezone, quiet_start: current.quiet ? current.quietStart : null, quiet_end: current.quiet ? current.quietEnd : null,
    digest_enabled: current.digest, digest_hour: current.digestHour,
    ...(current.token.trim() ? { token: current.token.trim() } : {}),
  })
  const save = useMutation({
    mutationFn: (body: NotificationSettingsUpdate) => api.updateNotificationSettings(body),
    onSuccess: saved => {
      setHasToken(saved.has_token)
      setDraft(current => current && { ...current, token: '' })
      client.setQueryData(['notification-settings'], saved)
    },
  })
  const test = useMutation({ mutationFn: () => api.testNotification() })
  const set = (change: Partial<Draft>) => setDraft(current => current && { ...current, ...change })
  const message = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback
  const data = settings.data

  return (
    <GlassPanel className="flex max-w-2xl flex-col gap-3" aria-label="Notifications">
      <h3 className="text-sm font-semibold text-foreground">Notifications</h3>
      <p className="text-sm text-muted-foreground">
        Push news from watched investigations to your phone or desktop through <a className="underline" href="https://ntfy.sh" target="_blank" rel="noreferrer">ntfy</a> (ntfy.sh or your own server). Install the ntfy app, subscribe to a topic only you know, and enter it here. Each investigation can be muted on its own page.
      </p>
      {settings.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {!settings.isPending && settings.isError && <LoadError query={settings} message="Could not load the notification settings." />}
      {draft && (
        <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); save.mutate(payload(draft)) }}>
          <label className="flex items-center gap-3 text-sm text-foreground">
            <input type="checkbox" className="h-4 w-4 accent-primary" checked={draft.enabled} onChange={event => set({ enabled: event.target.checked })} />
            Send notifications
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={labelClass}>ntfy server
              <input className={cn(fieldClass, 'mt-1')} type="url" placeholder="https://ntfy.sh" value={draft.server} onChange={event => set({ server: event.target.value })} />
            </label>
            <label className={labelClass}>Topic
              <input className={cn(fieldClass, 'mt-1')} placeholder="a-name-only-you-know" maxLength={64} value={draft.topic} onChange={event => set({ topic: event.target.value })} />
            </label>
            <label className={labelClass}>Access token
              <input className={cn(fieldClass, 'mt-1')} type="password" autoComplete="off" placeholder={hasToken ? 'A token is saved' : 'Only for a protected topic'} value={draft.token} onChange={event => set({ token: event.target.value })} />
            </label>
            <label className={labelClass}>App address
              <input className={cn(fieldClass, 'mt-1')} type="url" placeholder="https://newsintel.example" value={draft.publicUrl} onChange={event => set({ publicUrl: event.target.value })} />
            </label>
          </div>
          <p className="text-xs text-muted-foreground">The app address lets a message open the investigation it is about.</p>
          {hasToken && <button type="button" className={cn(ghostButtonClass, 'self-start')} disabled={save.isPending} onClick={() => save.mutate({ ...payload(draft), token: '' })}>Remove token</button>}
          <label className={labelClass}>Time zone
            <select className={cn(fieldClass, 'mt-1')} value={draft.timezone} onChange={event => set({ timezone: event.target.value })}>
              {zones.map(zone => <option key={zone} value={zone}>{zone}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-3 text-sm text-foreground">
            <input type="checkbox" className="h-4 w-4 accent-primary" checked={draft.quiet} onChange={event => set({ quiet: event.target.checked })} />
            Quiet hours
          </label>
          {draft.quiet && (
            <div className="flex flex-wrap items-end gap-3">
              <label className={labelClass}>From<input className={cn(fieldClass, 'mt-1')} type="time" value={draft.quietStart} onChange={event => set({ quietStart: event.target.value })} /></label>
              <label className={labelClass}>Until<input className={cn(fieldClass, 'mt-1')} type="time" value={draft.quietEnd} onChange={event => set({ quietEnd: event.target.value })} /></label>
              <p className="text-xs text-muted-foreground">News found in these hours is sent when they end.</p>
            </div>
          )}
          <label className="flex items-center gap-3 text-sm text-foreground">
            <input type="checkbox" className="h-4 w-4 accent-primary" checked={draft.digest} onChange={event => set({ digest: event.target.checked })} />
            Daily summary
          </label>
          {draft.digest && (
            <label className={labelClass}>Summary hour
              <select className={cn(fieldClass, 'mt-1')} value={draft.digestHour} onChange={event => set({ digestHour: Number(event.target.value) })}>
                {HOURS.map(hour => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
              </select>
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={primaryButtonClass} disabled={save.isPending}>Save notifications</button>
            <button type="button" className={ghostButtonClass} disabled={test.isPending} onClick={() => test.mutate()}>Send test message</button>
          </div>
          {save.isSuccess && <p className="text-sm text-primary">Notifications saved.</p>}
          {save.isError && <p role="alert" className="error text-sm text-destructive">{message(save.error, 'Could not save the notification settings.')}</p>}
          {test.isSuccess && <p className="text-sm text-primary">Test message sent. Check your device.</p>}
          {test.isError && <p role="alert" className="error text-sm text-destructive">{message(test.error, 'Could not send the test message.')}</p>}
        </form>
      )}
      {data?.last_sent_at && <p className="text-xs text-muted-foreground">Last sent {new Date(data.last_sent_at).toLocaleString()}</p>}
      {data?.last_error && <p className="error text-xs text-destructive">Last error: {data.last_error}{data.last_error_at ? ` (${new Date(data.last_error_at).toLocaleString()})` : ''}. Sending is tried again after five minutes.</p>}
    </GlassPanel>
  )
}
