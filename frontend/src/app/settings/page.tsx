'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../lib/api'
import type { StopWords } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { chipClass, fieldClass, ghostButtonClass, labelClass, primaryButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { LoadError } from '../../components/Feedback'
import { NotificationSettingsPanel } from '../../components/NotificationSettingsPanel'
import { useTheme, type ThemeChoice } from '../../lib/theme'
import { browserTimeZone, timeZoneNames, useTimeZone } from '../../lib/datetime'
import { plural } from '../../lib/utils'

type StopWordLanguage = 'en' | 'el'
const STOP_WORD_LANGUAGES: [StopWordLanguage, string][] = [['en', 'English'], ['el', 'Greek']]

/** One language's list. Keyed by language, so switching tabs starts from that list's own revision. */
function StopWordsEditor({ language, name }: { language: StopWordLanguage; name: string }) {
  const client = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const [stopWords, setStopWords] = useState<StopWords>()
  const [stopWordsError, setStopWordsError] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.stopWords(language).then(
      result => { if (!cancelled) { setStopWords(result); setDraft(result.words.join('\n')) } },
      () => { if (!cancelled) setStopWordsError(true) },
    )
    return () => { cancelled = true }
  }, [language])

  const save = useMutation({
    // Greek lower-cases with its own rules, which keep a final "ς".
    mutationFn: () => api.updateStopWords(stopWords!.revision, [...new Set((draft ?? '').split(/\r?\n/).map(word => word.trim().toLocaleLowerCase(language)).filter(Boolean))], language),
    onSuccess: data => { setStopWords(data); setDraft(data.words.join('\n')); client.invalidateQueries({ queryKey: ['nlp-status'] }) },
  })

  return (
    <>
      {!stopWords && !stopWordsError && <p className="text-sm text-muted-foreground">Loading stop words…</p>}
      {stopWordsError && <p className="error text-sm text-destructive">Could not load stop words.</p>}
      {stopWords && (
        <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); save.mutate() }}>
          <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
            {name} stop words
            <textarea className={cn(fieldClass, 'mt-1 resize-y font-mono')} value={draft ?? ''} onChange={event => setDraft(event.target.value)} rows={18} spellCheck={false} />
          </label>
          <p className="text-sm text-muted-foreground">One word per line · revision {stopWords.revision}</p>
          <button type="submit" disabled={save.isPending} className={cn(primaryButtonClass, 'self-start')}>Save stop words</button>
        </form>
      )}
      {save.isSuccess && <p className="text-sm text-primary">Stop words saved as revision {save.data?.revision}.</p>}
      {save.isError && <p className="error text-sm text-destructive">Could not save stop words. Reload the current revision and try again.</p>}
    </>
  )
}

const THEMES: [ThemeChoice, string][] = [['system', 'Same as the system'], ['light', 'Light'], ['dark', 'Dark']]

/** Kept by this browser, not the account: each device can follow its own system and zone. */
function AppearancePanel() {
  const { choice, setChoice } = useTheme()
  const timeZone = useTimeZone()
  return (
    <GlassPanel aria-label="Appearance" className="flex max-w-2xl flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Appearance</h3>
      <div role="radiogroup" aria-label="Theme" className="flex flex-wrap gap-4">
        {THEMES.map(([value, label]) => (
          <label key={value} className="flex items-center gap-2 text-sm text-foreground">
            <input type="radio" name="theme" className="h-4 w-4 accent-primary" checked={choice === value} onChange={() => setChoice(value)} />
            {label}
          </label>
        ))}
      </div>
      <label className={cn(labelClass, 'max-w-xs')}>Time zone for dates
        <select className={cn(fieldClass, 'mt-1')} value={timeZone.choice} onChange={event => timeZone.setChoice(event.target.value)}>
          <option value="browser">This browser ({browserTimeZone()})</option>
          {timeZoneNames().map(zone => <option key={zone} value={zone}>{zone}</option>)}
        </select>
      </label>
      <p className="text-xs text-muted-foreground">Saved in this browser. &ldquo;Same as the system&rdquo; turns dark when your desktop does. Charts that count per day count UTC days.</p>
    </GlassPanel>
  )
}

const errorText = (reason: unknown, fallback: string) => reason instanceof ApiError ? reason.message : fallback

function AccountPanel() {
  const client = useQueryClient()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => { setCurrent(''); setNext(''); setAgain(''); client.invalidateQueries({ queryKey: ['other-sessions'] }) },
  })
  const sessions = useQuery({ queryKey: ['other-sessions'], queryFn: api.otherSessions, retry: false })
  const signOut = useMutation({ mutationFn: api.signOutOtherSessions, onSuccess: () => client.invalidateQueries({ queryKey: ['other-sessions'] }) })
  const others = sessions.data?.others ?? 0

  return (
    <GlassPanel aria-label="Account" className="flex max-w-2xl flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Account</h3>
      <form className="flex flex-col gap-3" onSubmit={event => {
        event.preventDefault()
        change.reset()
        setMismatch(next !== again)
        if (next === again) change.mutate()
      }}>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className={labelClass}>Current password
            <input className={cn(fieldClass, 'mt-1')} type="password" autoComplete="current-password" required value={current} onChange={event => setCurrent(event.target.value)} />
          </label>
          <label className={labelClass}>New password
            <input className={cn(fieldClass, 'mt-1')} type="password" autoComplete="new-password" required minLength={12} value={next} onChange={event => setNext(event.target.value)} />
          </label>
          <label className={labelClass}>New password again
            <input className={cn(fieldClass, 'mt-1')} type="password" autoComplete="new-password" required minLength={12} value={again} onChange={event => setAgain(event.target.value)} />
          </label>
        </div>
        <p className="text-xs text-muted-foreground">At least 12 characters. Changing it signs out every other browser.</p>
        <button type="submit" className={cn(primaryButtonClass, 'self-start')} disabled={change.isPending}>Change password</button>
        {mismatch && <p role="alert" className="error text-sm text-destructive">The new passwords do not match.</p>}
        {change.isError && <p role="alert" className="error text-sm text-destructive">{errorText(change.error, 'Could not change the password.')}</p>}
        {change.isSuccess && <p className="text-sm text-primary">Password changed. Every other browser was signed out.</p>}
      </form>
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
        {sessions.isError && <LoadError query={sessions} message="Could not count the other browsers." />}
        {sessions.data && <p className="text-sm text-muted-foreground">{others ? `Signed in on ${plural(others, 'other browser')}.` : 'No other browser is signed in.'}</p>}
        <button type="button" className={ghostButtonClass} disabled={!others || signOut.isPending} onClick={() => signOut.mutate()}>Sign out other browsers</button>
        {signOut.isSuccess && <p className="text-sm text-primary">Signed out {plural(signOut.data.signed_out, 'browser')}.</p>}
        {signOut.isError && <p role="alert" className="error text-sm text-destructive">{errorText(signOut.error, 'Could not sign out the other browsers.')}</p>}
      </div>
    </GlassPanel>
  )
}

/** Wikidata is set on the server, so this only says where it stands and how to change it. */
function WikidataSettingsPanel() {
  const status = useQuery({ queryKey: ['ops-wikidata'], queryFn: api.opsWikidata, retry: false })
  return (
    <GlassPanel aria-label="Wikidata" className="flex max-w-2xl flex-col gap-3">
      <h3 className="text-sm font-semibold text-foreground">Wikidata</h3>
      {status.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
      {status.isError && <LoadError query={status} message="Could not load the Wikidata status." />}
      {status.data && (
        <p className="text-sm text-foreground">
          <span className={status.data.enabled ? 'text-primary' : 'text-muted-foreground'}>{status.data.enabled ? 'On' : `Off: ${status.data.reason ?? 'disabled'}`}</span>
          {` · ${plural(status.data.links, 'entity', 'entities')} linked`}
        </p>
      )}
      <p className="text-sm text-muted-foreground">
        Wikidata is set in the server&rsquo;s <code className="font-mono">.env</code>: <code className="font-mono">NEWSINTEL_WIKIDATA_CONTACT</code> (an email address or a URL, which Wikimedia asks for) turns it on, and <code className="font-mono">NEWSINTEL_WIKIDATA_ENABLED=false</code> turns it off. Restart the api, worker and scheduler after a change.
      </p>
      <Link className={cn(chipClass, 'self-start')} href="/processes/">Open Processes</Link>
    </GlassPanel>
  )
}

export default function SettingsPage() {
  const client = useQueryClient()
  const [stopWordLanguage, setStopWordLanguage] = useState<StopWordLanguage>('en')

  const greek = useQuery({ queryKey: ['greek-entities'], queryFn: api.greekEntities, retry: false })
  const [processExisting, setProcessExisting] = useState(true)
  const toggleGreek = useMutation({
    mutationFn: (enabled: boolean) => api.updateGreekEntities(enabled, processExisting),
    onSuccess: data => { client.setQueryData(['greek-entities'], data); client.invalidateQueries({ queryKey: ['nlp-status'] }) },
  })
  const greekOn = greek.data?.enabled ?? false

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="Preferences" title="Settings" />
      <AppearancePanel />
      <AccountPanel />
      <GlassPanel className="flex max-w-2xl flex-col gap-3">
        <h3 className="text-sm font-semibold text-foreground">Greek entities</h3>
        <p className="text-sm text-muted-foreground">Find people, places and organizations in Greek articles. English articles are not affected. Turning this off keeps the Greek entities already found.</p>
        {greek.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {!greek.isPending && greek.isError && <LoadError query={greek} message="Could not load the Greek entities setting." />}
        {greek.data && (
          <>
            <label className="flex items-center gap-3 text-sm text-foreground">
              <input type="checkbox" role="switch" className="h-4 w-4 accent-primary" checked={greekOn} disabled={toggleGreek.isPending || (!greekOn && !greek.data.available)} onChange={event => toggleGreek.mutate(event.target.checked)} />
              Greek entities {greekOn ? 'on' : 'off'}
            </label>
            {!greek.data.available && <p className="text-sm text-muted-foreground">Not available: {greek.data.detail}</p>}
            {!greekOn && greek.data.available && (
              <label className="flex items-center gap-3 text-sm text-muted-foreground">
                <input type="checkbox" className="h-4 w-4 accent-primary" checked={processExisting} onChange={event => setProcessExisting(event.target.checked)} />
                Also process the {greek.data.greek_article_count} Greek articles already in the archive
              </label>
            )}
          </>
        )}
        {toggleGreek.isSuccess && toggleGreek.data.enabled && (
          <p className="text-sm text-primary">Greek entities are on.{toggleGreek.data.queued_article_count > 0 && ` ${toggleGreek.data.queued_article_count} Greek articles will be processed in the background.`}</p>
        )}
        {toggleGreek.isSuccess && !toggleGreek.data.enabled && <p className="text-sm text-primary">Greek entities are off.</p>}
        {toggleGreek.isError && <p className="error text-sm text-destructive">Could not change the setting. {toggleGreek.error.message}</p>}
      </GlassPanel>
      <NotificationSettingsPanel />
      <WikidataSettingsPanel />
      <GlassPanel className="flex max-w-2xl flex-col gap-3">
        <h3 className="text-sm font-semibold text-foreground">Stop words</h3>
        <p className="text-sm text-muted-foreground">Words left out of keywords and story titles, one list per language. Changes affect new processing immediately. Existing annotations require reprocessing before they reflect this revision.</p>
        <div role="tablist" aria-label="Stop word language" className="flex gap-2">
          {STOP_WORD_LANGUAGES.map(([code, label]) => (
            <button key={code} type="button" role="tab" aria-selected={stopWordLanguage === code} className={cn(ghostButtonClass, stopWordLanguage === code && 'border-ring text-foreground')} onClick={() => setStopWordLanguage(code)}>{label}</button>
          ))}
        </div>
        <StopWordsEditor key={stopWordLanguage} language={stopWordLanguage} name={STOP_WORD_LANGUAGES.find(([code]) => code === stopWordLanguage)![1]} />
      </GlassPanel>
    </div>
  )
}
