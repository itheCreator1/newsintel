import type { SearchFacets } from '../lib/api-types'
import type { Investigation, ListField } from '../lib/investigation'
import { chipClass } from '../lib/ui-classes'
import { cn } from '../lib/utils'

type FacetGroupKey = Exclude<keyof SearchFacets, 'total'>

/** Each facet group and the existing URL filter its values toggle. */
export const FACET_GROUPS: { group: FacetGroupKey; field: ListField; title: string }[] = [
  { group: 'sources', field: 'source_id', title: 'Sources' },
  { group: 'source_countries', field: 'source_country', title: 'Source countries' },
  { group: 'story_countries', field: 'story_country', title: 'Story countries' },
  { group: 'mentioned_countries', field: 'mentioned_country', title: 'Mentioned countries' },
  { group: 'languages', field: 'language', title: 'Languages' },
  { group: 'entities', field: 'entity_id', title: 'Entities' },
  { group: 'entity_types', field: 'entity_type', title: 'Entity types' },
  { group: 'keywords', field: 'keyword_id', title: 'Keywords' },
  { group: 'story_clusters', field: 'story_cluster_id', title: 'Stories' },
]

/** Bounded facets for the current investigation; each value toggles its existing URL filter. */
export function FacetPanel({ facets, state, onToggle }: { facets: SearchFacets; state: Investigation; onToggle: (field: ListField, value: string) => void }) {
  const groups = FACET_GROUPS.filter(({ group }) => facets[group].buckets.length)
  if (!groups.length) return <p className="text-sm text-muted-foreground">No facets for this search.</p>
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
      {groups.map(({ group, field, title }) => (
        <section key={group} aria-labelledby={`facet-${group}`} className="flex flex-col gap-2">
          <h4 id={`facet-${group}`} className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h4>
          <ul className="flex flex-wrap gap-2">
            {facets[group].buckets.map(bucket => {
              const active = state[field].includes(bucket.value)
              return (
                <li key={bucket.value}>
                  <button type="button" aria-pressed={active} className={cn(chipClass, 'w-auto', active && 'border-primary text-primary')} onClick={() => onToggle(field, bucket.value)}>
                    {bucket.label ?? bucket.value} <span className="text-muted-foreground">{bucket.count}</span>
                  </button>
                </li>
              )
            })}
          </ul>
          {facets[group].truncated && <p className="text-[11px] text-muted-foreground">Top {facets[group].buckets.length} shown.</p>}
        </section>
      ))}
    </div>
  )
}
