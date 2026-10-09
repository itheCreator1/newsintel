import { ApiError } from '../../lib/api'

export const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'

export function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="flex flex-col gap-0.5"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="m-0 font-mono text-sm text-foreground">{value}</dd></div>
}

export function AsOf({ at }: { at: string }) {
  return <span className="text-xs text-muted-foreground">As of {when(at)}</span>
}

/** The server's reason when it gave one (a refused action says why), else a plain failure. */
export function failure(error: unknown, fallback: string): string {
  return (error instanceof ApiError || error instanceof Error) && error.message && error.message !== 'Request failed' ? error.message : fallback
}

export const count = (value: number) => value.toLocaleString('en')
