'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { StopWords } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { fieldClass, ghostButtonClass, primaryButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'
import { LoadError } from '../../components/Feedback'
import { NotificationSettingsPanel } from '../../components/NotificationSettingsPanel'

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

export default function SettingsPage() {
  const client = useQueryClient()
  const [stopWordLanguage, setStopWordLanguage] = useState<StopWordLanguage>('en')
  const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, retry: false })

  const greek = useQuery({ queryKey: ['greek-entities'], queryFn: api.greekEntities, retry: false })
  const [processExisting, setProcessExisting] = useState(true)
  const toggleGreek = useMutation({
    mutationFn: (enabled: boolean) => api.updateGreekEntities(enabled, processExisting),
    onSuccess: data => { client.setQueryData(['greek-entities'], data); client.invalidateQueries({ queryKey: ['nlp-status'] }) },
  })
  const greekOn = greek.data?.enabled ?? false

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="NLP configuration" title="Settings" />
      <GlassPanel className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-foreground">Processor capabilities</h3>
        {nlp.isPending && <p className="text-sm text-muted-foreground">Loading capabilities…</p>}
        {!nlp.isPending && nlp.isError && <LoadError query={nlp} message="Could not load processor capabilities." />}
        {nlp.data?.capabilities.map(capability => (
          <p key={capability.name} className="text-sm text-muted-foreground">{capability.name} · {capability.state}{capability.version && ` · ${capability.version}`}{capability.detail && ` · ${capability.detail}`}</p>
        ))}
      </GlassPanel>
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
