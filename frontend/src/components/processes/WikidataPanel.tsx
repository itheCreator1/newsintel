import type { UseQueryResult } from '@tanstack/react-query'
import type { OpsWikidata } from '../../lib/api-types'
import { GlassPanel } from '../GlassPanel'
import { LoadError, Note } from '../Feedback'
import { StatusBadge } from '../StatusBadge'
import { Stat, when } from './parts'

const THROTTLE_TONE = { open: 'healthy', paused: 'degraded', budget_spent: 'degraded' } as const

function throttleText({ state, paused_until }: OpsWikidata['throttle']): string {
  if (state === 'paused') return `Paused until ${when(paused_until)}`
  if (state === 'budget_spent') return 'Today’s budget is spent'
  return 'Open'
}

/** How much Wikidata is asked and whether it may be: the pause, today's budget, links and runs. */
function WikidataDetails({ data }: { data: OpsWikidata }) {
  const { throttle } = data
  const num = 'py-1.5 pr-4 font-mono'
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 px-6">
        {data.enabled
          ? <StatusBadge tone={THROTTLE_TONE[throttle.state]}>{throttleText(throttle)}</StatusBadge>
          : <StatusBadge tone="pending">{`Off: ${data.reason ?? 'disabled'}`}</StatusBadge>}
        {throttle.pause_reason && <span className="text-xs text-muted-foreground">{`Reason: ${throttle.pause_reason}`}</span>}
      </div>
      <p className="px-6 text-xs text-muted-foreground">Requests go one at a time, at least 3 seconds apart, within a daily budget; a 429, 503 or maxlag answer pauses them. Linked items are checked again every month.</p>
      <dl className="grid grid-cols-2 gap-3 px-6 sm:grid-cols-5">
        <Stat label="Requests today" value={`${throttle.requests_today} of ${throttle.daily_budget}`} />
        <Stat label="Linked entities" value={data.links} />
        <Stat label="Open suggestions" value={data.open_candidates} />
        <Stat label="Due for refresh" value={data.due_refresh} />
        <Stat label="Last refresh" value={when(data.last_refresh?.finished_at)} />
      </dl>
      {data.runs.length > 0 && (
        <div className="overflow-x-auto px-6">
          <table aria-label="Wikidata runs" className="w-full text-left text-sm">
            <thead><tr className="text-xs text-muted-foreground">{['Kind', 'Status', 'Checked', 'Changed', 'Redirected', 'Missing', 'Requests', 'Created'].map(head => <th key={head} className="py-2 pr-4 font-medium">{head}</th>)}</tr></thead>
            <tbody>
              {data.runs.map(run => (
                <tr key={run.id} className="border-t border-border align-top">
                  <th scope="row" className="py-1.5 pr-4 text-left font-normal text-foreground">{run.kind}</th>
                  <td className="py-1.5 pr-4">{run.status}{run.error && <span className="block text-xs text-destructive">{run.error}</span>}</td>
                  <td className={num}>{run.checked}</td>
                  <td className={num}>{run.changed}</td>
                  <td className={num}>{run.redirected}</td>
                  <td className={num}>{run.missing}</td>
                  <td className={num}>{run.requests}</td>
                  <td className="py-1.5 text-xs text-muted-foreground">{when(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.counts.length > 0 && (
        <div className="overflow-x-auto px-6 pb-6">
          <table aria-label="Wikidata requests" className="w-full text-left text-sm">
            <thead><tr className="text-xs text-muted-foreground">{['Day', 'Kind', 'Outcome', 'Requests', 'Average'].map(head => <th key={head} className="py-2 pr-4 font-medium">{head}</th>)}</tr></thead>
            <tbody>
              {data.counts.map(count => (
                <tr key={`${count.day}-${count.kind}-${count.outcome}`} className="border-t border-border">
                  <th scope="row" className="py-1.5 pr-4 text-left font-normal text-foreground">{count.day}</th>
                  <td className="py-1.5 pr-4">{count.kind}</td>
                  <td className="py-1.5 pr-4">{count.outcome}</td>
                  <td className={num}>{count.count}</td>
                  <td className="py-1.5 font-mono">{`${count.average_ms} ms`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

export function WikidataPanel({ wikidata }: { wikidata: UseQueryResult<OpsWikidata> }) {
  return (
    <GlassPanel aria-label="Wikidata" className="flex flex-col gap-3 p-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
        <h3 className="text-lg font-semibold text-foreground">Wikidata</h3>
      </div>
      {wikidata.isPending && <Note>Loading Wikidata…</Note>}
      {wikidata.isError && <LoadError className="px-6 py-4" query={wikidata} message="Could not load the Wikidata status." />}
      {wikidata.data && <WikidataDetails data={wikidata.data} />}
    </GlassPanel>
  )
}
