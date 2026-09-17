'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { StopWords } from '../../lib/api-types'
import { GlassPanel } from '../../components/GlassPanel'
import { PageHeader } from '../../components/PageHeader'
import { fieldClass, primaryButtonClass } from '../../lib/ui-classes'
import { cn } from '../../lib/utils'

export default function SettingsPage() {
  const client = useQueryClient()
  const [draft, setDraft] = useState<string | null>(null)
  const [stopWords, setStopWords] = useState<StopWords>()
  const [stopWordsError, setStopWordsError] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.stopWords().then(
      result => { if (!cancelled) { setStopWords(result); setDraft(result.words.join('\n')) } },
      () => { if (!cancelled) setStopWordsError(true) },
    )
    return () => { cancelled = true }
  }, [])

  const nlp = useQuery({ queryKey: ['nlp-status'], queryFn: api.nlpStatus, retry: false })
  const save = useMutation({
    mutationFn: () => api.updateStopWords(stopWords!.revision, [...new Set((draft ?? '').split(/\r?\n/).map(word => word.trim().toLocaleLowerCase('en')).filter(Boolean))]),
    onSuccess: data => { setStopWords(data); setDraft(data.words.join('\n')); client.invalidateQueries({ queryKey: ['nlp-status'] }) },
  })

  return (
    <div className="flex flex-col gap-6 font-sans">
      <PageHeader eyebrow="NLP configuration" title="Settings" />
      <GlassPanel className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold text-foreground">Processor capabilities</h3>
        {nlp.isPending && <p className="text-sm text-muted-foreground">Loading capabilities…</p>}
        {!nlp.isPending && nlp.isError && <p className="error text-sm text-destructive">Could not load processor capabilities.</p>}
        {nlp.data?.capabilities.map(capability => (
          <p key={capability.name} className="text-sm text-muted-foreground">{capability.name} · {capability.state}{capability.version && ` · ${capability.version}`}{capability.detail && ` · ${capability.detail}`}</p>
        ))}
      </GlassPanel>
      <GlassPanel className="flex max-w-2xl flex-col gap-3">
        <h3 className="text-sm font-semibold text-foreground">English stop words</h3>
        <p className="text-sm text-muted-foreground">Changes affect new processing immediately. Existing annotations require reprocessing before they reflect this revision.</p>
        {!stopWords && !stopWordsError && <p className="text-sm text-muted-foreground">Loading stop words…</p>}
        {stopWordsError && <p className="error text-sm text-destructive">Could not load stop words.</p>}
        {stopWords && (
          <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); save.mutate() }}>
            <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
              English stop words
              <textarea className={cn(fieldClass, 'mt-1 resize-y font-mono')} value={draft ?? ''} onChange={event => setDraft(event.target.value)} rows={18} spellCheck={false} />
            </label>
            <p className="text-sm text-muted-foreground">One word per line · revision {stopWords.revision}</p>
            <button type="submit" disabled={save.isPending} className={cn(primaryButtonClass, 'self-start')}>Save stop words</button>
          </form>
        )}
        {save.isSuccess && <p className="text-sm text-primary">Stop words saved as revision {save.data?.revision}.</p>}
        {save.isError && <p className="error text-sm text-destructive">Could not save stop words. Reload the current revision and try again.</p>}
      </GlassPanel>
    </div>
  )
}
