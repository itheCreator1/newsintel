import type { UseQueryResult } from '@tanstack/react-query'
import type { OpsHealth } from '../../lib/api-types'
import { probeTone } from '../../lib/operations'
import { GlassPanel } from '../GlassPanel'
import { LoadError, Note } from '../Feedback'
import { StatusBadge } from '../StatusBadge'
import { AsOf } from './parts'

const NAMES: Record<string, string> = { postgres: 'PostgreSQL', redis: 'Redis', elasticsearch: 'Elasticsearch', nlp: 'NLP models', scheduler: 'Scheduler', workers: 'Workers' }
const STATES = { ok: 'OK', degraded: 'Degraded', down: 'Down', unknown: 'Unknown' } as const

/** The services the processes need: each is checked under a 2 second limit, so an outage shows as Down. */
export function Services({ health }: { health: UseQueryResult<OpsHealth> }) {
  return (
    <GlassPanel aria-label="Services" className="flex flex-col gap-3 p-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-6 pt-6">
        <h3 className="text-lg font-semibold text-foreground">Services</h3>
        {health.data && <AsOf at={health.data.generated_at} />}
      </div>
      {health.isPending && <Note>Checking services…</Note>}
      {health.isError && <LoadError className="px-6 py-4" query={health} message="Could not check the services." />}
      {health.data && (
        <>
          <ul className="grid list-none grid-cols-2 gap-2.5 px-6 xl:grid-cols-3 2xl:grid-cols-6">
            {health.data.probes.map(probe => (
              <li key={probe.name} className="flex flex-col gap-1 rounded-lg border border-border px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-foreground">{NAMES[probe.name] ?? probe.name}</span>
                  <StatusBadge tone={probeTone(probe.state)}>{STATES[probe.state]}</StatusBadge>
                </div>
                {probe.latency_ms !== null && <span className="font-mono text-xs text-muted-foreground">{probe.latency_ms} ms</span>}
                {probe.detail && <span className="text-xs text-muted-foreground">{probe.detail}</span>}
              </li>
            ))}
          </ul>
          <p className="px-6 text-xs text-muted-foreground">Workers checks that each queue has a live worker process; whether it keeps up shows in each card’s oldest wait and expired leases.</p>
          {health.data.queues.length > 0 && (
            <div className="overflow-x-auto px-6 pb-6">
              <table aria-label="Queues" className="w-full text-left text-sm">
                <thead><tr className="text-xs text-muted-foreground"><th className="py-2 pr-4 font-medium">Queue</th><th className="py-2 pr-4 font-medium">Ready</th><th className="py-2 pr-4 font-medium">Delayed</th><th className="py-2 font-medium">Dead-lettered</th></tr></thead>
                <tbody>{health.data.queues.map(queue => <tr key={queue.queue} className="border-t border-border"><th scope="row" className="py-1.5 pr-4 text-left font-normal text-foreground">{queue.queue}</th><td className="py-1.5 pr-4 font-mono">{queue.ready}</td><td className="py-1.5 pr-4 font-mono">{queue.delayed}</td><td className="py-1.5 font-mono">{queue.dead}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </GlassPanel>
  )
}
