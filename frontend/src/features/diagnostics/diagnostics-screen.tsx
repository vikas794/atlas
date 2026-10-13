import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  RefreshCw,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import { getDiagnostics } from '../../lib/api'
import { errorMessage, isNotFound } from '../../lib/errors'
import { pluralize } from '../../lib/format'
import { SLOW_POLL_MS } from '../../lib/polling'
import { queryKeys } from '../../lib/query-keys'
import { TONE_TEXT, type Tone } from '../../lib/tone'
import type { DiagnosticCheck, DiagnosticLogEntry } from '../../lib/types'
import { cn } from '../../lib/utils'
import { RelativeTime } from '../../components/shared/relative-time'
import {
  EmptyState,
  ErrorState,
  SkeletonListRows,
  SkeletonTableRows,
} from '../../components/shared/state-panels'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'

interface StatusSpec {
  label: string
  tone: Tone
  Icon: LucideIcon
}

/** Check status -> icon + label + colour (design.md 8: never colour alone). */
const CHECK_STATUS: Record<DiagnosticCheck['status'], StatusSpec> = {
  ok: { label: 'OK', tone: 'success', Icon: CheckCircle2 },
  warning: { label: 'Warning', tone: 'warn', Icon: AlertTriangle },
  error: { label: 'Error', tone: 'error', Icon: XCircle },
}

const UNKNOWN_STATUS: StatusSpec = { label: 'Unknown', tone: 'neutral', Icon: HelpCircle }

function checkStatus(status: string): StatusSpec {
  return CHECK_STATUS[status as DiagnosticCheck['status']] ?? UNKNOWN_STATUS
}

/** Log level -> badge tone; anything unexpected stays neutral. */
function levelTone(level: string): Tone {
  const upper = level.toUpperCase()
  if (upper === 'ERROR' || upper === 'CRITICAL') return 'error'
  if (upper === 'WARNING' || upper === 'WARN') return 'warn'
  return 'neutral'
}

function CheckRow({ check }: { check: DiagnosticCheck }) {
  const spec = checkStatus(check.status)
  const { Icon } = spec
  return (
    <li className="flex items-start gap-3 border-b border-border px-4 py-3 last:border-b-0">
      <Icon aria-hidden="true" className={cn('mt-1 size-icon shrink-0', TONE_TEXT[spec.tone])} />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium text-text">{check.label}</p>
        {check.message ? <p className="text-label text-text-muted">{check.message}</p> : null}
      </div>
      <Badge tone={spec.tone} className="shrink-0">
        {spec.label}
      </Badge>
    </li>
  )
}

function ErrorRow({ entry }: { entry: DiagnosticLogEntry }) {
  return (
    <tr className="border-b border-border align-top last:border-b-0">
      <td className="whitespace-nowrap px-3 py-2 text-label text-text-muted">
        <RelativeTime iso={entry.timestamp} />
      </td>
      <td className="px-3 py-2">
        <Badge tone={levelTone(entry.level)}>{entry.level}</Badge>
      </td>
      <td className="px-3 py-2 font-mono text-label text-text-muted">{entry.logger}</td>
      <td className="break-words px-3 py-2 text-body text-text">{entry.message}</td>
    </tr>
  )
}

/**
 * Diagnostics (GET /api/diagnostics): readiness checks and the most recent
 * backend warnings and errors, so a failing setup explains itself without reading logs.
 */
export function DiagnosticsScreen() {
  const query = useQuery({
    queryKey: queryKeys.diagnostics,
    queryFn: getDiagnostics,
    refetchInterval: SLOW_POLL_MS,
  })
  const data = query.data
  const counts = data
    ? {
        ok: data.checks.filter((check) => check.status === 'ok').length,
        warning: data.checks.filter((check) => check.status === 'warning').length,
        error: data.checks.filter((check) => check.status === 'error').length,
      }
    : null

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-page text-text">Diagnostics</h1>
        <Button
          variant="secondary"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          <RefreshCw
            aria-hidden="true"
            className={cn('size-icon', query.isFetching && 'atlas-spin')}
          />
          {query.isFetching ? 'Checking…' : 'Run checks again'}
        </Button>
      </div>

      {query.isError && !data ? (
        <ErrorState
          title="Could not load diagnostics"
          message={
            isNotFound(query.error)
              ? 'This backend does not provide diagnostics (GET /api/diagnostics returned 404). Update the backend, then try again.'
              : errorMessage(query.error)
          }
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          <section className="space-y-3" aria-labelledby="checks-heading">
            <div className="flex items-center justify-between">
              <h2 id="checks-heading" className="text-section text-text">
                Checks
              </h2>
              {counts ? (
                <span className="text-label tabular-nums text-text-muted">
                  {counts.ok} OK, {pluralize(counts.warning, 'warning')},{' '}
                  {pluralize(counts.error, 'error')}
                </span>
              ) : null}
            </div>
            <div className="rounded border border-border bg-surface">
              {!data ? (
                <SkeletonListRows rows={4} />
              ) : data.checks.length === 0 ? (
                <p className="p-4 text-body text-text-muted">The backend reported no checks.</p>
              ) : (
                <ul aria-label="Diagnostic checks">
                  {data.checks.map((check) => (
                    <CheckRow key={check.id} check={check} />
                  ))}
                </ul>
              )}
            </div>
          </section>

          <section className="space-y-3" aria-labelledby="errors-heading">
            <div>
              <h2 id="errors-heading" className="text-section text-text">
                Recent warnings and errors
              </h2>
              <p className="text-label text-text-muted">
                The last 50 backend log records at WARNING or above, newest first. Rejected requests
                (4xx) are logged at INFO, so they do not appear here.
              </p>
            </div>
            {data && data.recent_errors.length === 0 ? (
              <EmptyState title="No warnings or errors logged recently." />
            ) : (
              <div className="overflow-x-auto rounded border border-border bg-surface">
                <table className="w-full text-left text-body">
                  <thead className="border-b border-border bg-surface-muted text-label text-text">
                    <tr className="h-row">
                      <th scope="col" className="px-3">
                        Time
                      </th>
                      <th scope="col" className="px-3">
                        Level
                      </th>
                      <th scope="col" className="px-3">
                        Source
                      </th>
                      <th scope="col" className="px-3">
                        Message
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {!data ? (
                      <SkeletonTableRows rows={3} widths={['w-16', 'w-12', 'w-24', 'w-64']} />
                    ) : (
                      // Already newest first from the backend; keep its order.
                      data.recent_errors.map((entry, index) => (
                        <ErrorRow key={`${entry.timestamp}-${index}`} entry={entry} />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {query.isError && data ? (
            <p role="alert" className="text-label text-st-error-fg">
              Showing the last successful result. Refreshing failed: {errorMessage(query.error)}
            </p>
          ) : null}
        </>
      )}
    </div>
  )
}
