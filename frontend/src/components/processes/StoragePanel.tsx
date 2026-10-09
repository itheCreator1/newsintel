import type { UseQueryResult } from '@tanstack/react-query'
import type { OpsStorage } from '../../lib/api-types'
import { bytes } from '../../lib/operations'
import { plural } from '../../lib/utils'
import { GlassPanel } from '../GlassPanel'
import { LoadError, Note } from '../Feedback'
import { AsOf } from './parts'

/** What the archive takes up: the database, its largest tables and the search index. */
export function StoragePanel({ storage }: { storage: UseQueryResult<OpsStorage> }) {
  return (
    <GlassPanel aria-label="Storage" className="flex flex-col gap-3 p-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
        <h3 className="text-lg font-semibold text-foreground">Storage</h3>
        {storage.data && <AsOf at={storage.data.generated_at} />}
      </div>
      {storage.isPending && <Note>Loading storage…</Note>}
      {storage.isError && <LoadError className="px-6 py-4" query={storage} message="Could not load storage." />}
      {storage.data && (
        <>
          <p className="px-6 text-sm text-foreground">Database {bytes(storage.data.database_bytes)}</p>
          <p className="px-6 text-sm text-foreground">
            {storage.data.elasticsearch
              ? `Elasticsearch ${storage.data.elasticsearch.index}: ${plural(storage.data.elasticsearch.documents, 'document')}, ${bytes(storage.data.elasticsearch.store_bytes)}`
              : `Elasticsearch could not be measured (${storage.data.elasticsearch_error ?? 'no index'}).`}
          </p>
          <p className="px-6 text-sm text-foreground">{plural(storage.data.retained_html_objects, 'retained HTML object')} recorded</p>
          <p className="px-6 text-xs text-muted-foreground">{storage.data.article_files_note}</p>
          <div className="overflow-x-auto px-6 pb-6">
            <table aria-label="Tables" className="w-full text-left text-sm">
              <thead><tr className="text-xs text-muted-foreground"><th className="py-2 pr-4 font-medium">Table</th><th className="py-2 pr-4 font-medium">Size with indexes</th><th className="py-2 font-medium">Rows (planner estimate)</th></tr></thead>
              <tbody>{storage.data.tables.map(table => <tr key={table.name} className="border-t border-border"><td className="py-1.5 pr-4 text-foreground">{table.name}</td><td className="py-1.5 pr-4 font-mono">{bytes(table.total_bytes)}</td><td className="py-1.5 font-mono">≈ {table.approximate_rows}</td></tr>)}</tbody>
            </table>
          </div>
        </>
      )}
    </GlassPanel>
  )
}
