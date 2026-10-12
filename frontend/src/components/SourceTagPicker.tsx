'use client'

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { CheckboxPicker } from './CheckboxPicker'

/** The tags given to sources on the Sources page; picking one narrows to the sources carrying it. */
export function SourceTagPicker({ selected, onChange }: { selected: string[]; onChange: (values: string[]) => void }) {
  const tags = useQuery({ queryKey: ['feeds', 'tags'], queryFn: api.feedTags })
  return (
    <CheckboxPicker
      legend="Source tag"
      options={(tags.data ?? []).map(item => ({ value: item.tag, label: `${item.tag} (${item.count})` }))}
      selected={selected}
      onChange={onChange}
      emptyText={tags.isPending ? 'Loading tags…' : 'No source has a tag yet. Add tags on the Sources page.'}
    />
  )
}
