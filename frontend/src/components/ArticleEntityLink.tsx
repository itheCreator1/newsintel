'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { SeeAlsoLabel } from '../lib/api-types'
import { SEE_ALSO_LABELS } from '../lib/see-also'
import { fieldClass, ghostButtonClass, labelClass } from '../lib/ui-classes'

interface ArticleEntity { id: string; text: string; entity_type: string }

/** States a see-also link between two entities this article names, citing the article as its source. */
export function ArticleEntityLink({ articleId, entities }: { articleId: string; entities: ArticleEntity[] }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [from, setFrom] = useState('')
  const [label, setLabel] = useState<SeeAlsoLabel | ''>('')
  const [target, setTarget] = useState('')
  const [note, setNote] = useState('')
  const [message, setMessage] = useState('')
  // The link types depend on the first entity's type, so they come from its see-also section.
  const links = useQuery({ queryKey: ['entity-see-also', from], queryFn: () => api.seeAlso(from), enabled: Boolean(from), retry: false })
  const chosen = label || links.data?.labels[0] || ''
  const name = (id: string) => entities.find(entity => entity.id === id)?.text ?? id
  const save = useMutation({
    mutationFn: () => api.addRelation(from, { label: chosen as SeeAlsoLabel, target_id: target, source_article_id: articleId, ...(note.trim() ? { note: note.trim() } : {}) }),
    onSuccess: () => {
      setMessage(`Linked ${name(from)} to ${name(target)}, with this article as the source.`)
      setOpen(false)
      return Promise.all(['entity-see-also', 'entity-history', 'authority-history'].map(key => client.invalidateQueries({ queryKey: [key] })))
    },
  })

  function start() {
    save.reset(); setMessage('')
    setFrom(''); setLabel(''); setTarget(''); setNote('')
    setOpen(true)
  }

  if (entities.length < 2) return null
  return (
    <div className="flex flex-col gap-2">
      {!open && <div><button type="button" className={ghostButtonClass} onClick={start}>Link two entities</button></div>}
      {open && (
        <form aria-label="Link entities from this article" className="flex flex-col gap-3 rounded-lg border border-border p-3" onSubmit={event => { event.preventDefault(); save.mutate() }}>
          <div className="flex flex-wrap gap-3">
            <label className={labelClass}>Entity
              <select className={fieldClass} value={from} onChange={event => { setFrom(event.target.value); setLabel(''); if (target === event.target.value) setTarget('') }}>
                <option value="">Choose an entity</option>
                {entities.map(entity => <option key={entity.id} value={entity.id}>{`${entity.text} (${entity.entity_type})`}</option>)}
              </select>
            </label>
            {links.data && (
              <label className={labelClass}>Link type
                <select className={fieldClass} value={chosen} onChange={event => setLabel(event.target.value as SeeAlsoLabel)}>
                  {links.data.labels.map(option => <option key={option} value={option}>{SEE_ALSO_LABELS[option]}</option>)}
                </select>
              </label>
            )}
            <label className={labelClass}>Linked entity
              <select className={fieldClass} value={target} onChange={event => setTarget(event.target.value)}>
                <option value="">Choose an entity</option>
                {entities.filter(entity => entity.id !== from).map(entity => <option key={entity.id} value={entity.id}>{`${entity.text} (${entity.entity_type})`}</option>)}
              </select>
            </label>
          </div>
          {/* A plain "related" link must say how the two relate; the service refuses it otherwise. */}
          <label className={labelClass}>Link note
            <textarea className={fieldClass} rows={2} maxLength={4000} value={note} onChange={event => setNote(event.target.value)} />
          </label>
          {links.isError && <p className="error text-sm text-destructive">Could not load the link types.</p>}
          <div className="flex gap-2">
            <button type="submit" className={ghostButtonClass} disabled={save.isPending || !from || !target || !chosen}>Save link</button>
            <button type="button" className={ghostButtonClass} onClick={() => setOpen(false)}>Cancel</button>
          </div>
          {save.isError && <p role="alert" className="error text-sm text-destructive">{save.error instanceof ApiError ? save.error.message : 'Could not save this link.'}</p>}
        </form>
      )}
      {message && <p role="status" className="text-sm text-primary">{message}</p>}
    </div>
  )
}
