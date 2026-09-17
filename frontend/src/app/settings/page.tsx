'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { StopWords } from '../../lib/api-types'

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
    <>
      <header><div><p className="eyebrow">NLP configuration</p><h2>Settings</h2></div></header>
      <section className="panel settings-panel">
        <h3>Processor capabilities</h3>
        {nlp.isPending && <p className="muted">Loading capabilities…</p>}
        {!nlp.isPending && nlp.isError && <p className="error">Could not load processor capabilities.</p>}
        {nlp.data?.capabilities.map(capability => (
          <p key={capability.name} className="muted">{capability.name} · {capability.state}{capability.version && ` · ${capability.version}`}{capability.detail && ` · ${capability.detail}`}</p>
        ))}
      </section>
      <section className="panel settings-panel">
        <h3>English stop words</h3>
        <p>Changes affect new processing immediately. Existing annotations require reprocessing before they reflect this revision.</p>
        {!stopWords && !stopWordsError && <p className="muted">Loading stop words…</p>}
        {stopWordsError && <p className="error">Could not load stop words.</p>}
        {stopWords && (
          <form onSubmit={event => { event.preventDefault(); save.mutate() }}>
            <label>English stop words<textarea value={draft ?? ''} onChange={event => setDraft(event.target.value)} rows={18} spellCheck={false} /></label>
            <p className="muted">One word per line · revision {stopWords.revision}</p>
            <button type="submit" disabled={save.isPending}>Save stop words</button>
          </form>
        )}
        {save.isSuccess && <p className="success">Stop words saved as revision {save.data?.revision}.</p>}
        {save.isError && <p className="error">Could not save stop words. Reload the current revision and try again.</p>}
      </section>
    </>
  )
}
