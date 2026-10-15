import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Check,
  ChevronDown,
  Copy,
  HelpCircle,
  RefreshCw,
  Trash2,
  XCircle,
  type LucideIcon,
} from 'lucide-react'

import { clearRecentErrors, getDiagnostics } from '../../lib/api'
import { errorMessage, isNotFound } from '../../lib/errors'
import { pluralize } from '../../lib/format'
import { SLOW_POLL_MS } from '../../lib/polling'
import { queryKeys } from '../../lib/query-keys'
import { TONE_TEXT, type Tone } from '../../lib/tone'
import type { DiagnosticCheck, DiagnosticLogEntry, DiagnosticsResponse } from '../../lib/types'
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

/** design.md 4: sentence case everywhere ("WARNING" -> "Warning"). */
function levelLabel(level: string): string {
  return level ? level.charAt(0).toUpperCase() + level.slice(1).toLowerCase() : level
}

type LevelFilter = 'all' | 'warning' | 'error'

const FILTERS: { value: LevelFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'warning', label: 'Warnings' },
  { value: 'error', label: 'Errors' },
]

function matchesFilter(entry: DiagnosticLogEntry, filter: LevelFilter): boolean {
  if (filter === 'all') return true
  return levelTone(entry.level) === (filter === 'error' ? 'error' : 'warn')
}

/** Plain-text report of the problems on screen, ready to paste to whoever fixes them. */
function buildReport(data: DiagnosticsResponse, entries: DiagnosticLogEntry[]): string {
  const problems = data.checks.filter((check) => check.status !== 'ok')
  const lines = [`Atlas diagnostics (${new Date().toISOString()})`, '']
  lines.push('Checks needing attention:')
  if (problems.length === 0) lines.push('- none')
  for (const check of problems) {
    lines.push(`- [${check.status.toUpperCase()}] ${check.label}: ${check.message}`)
  }
  lines.push('', 'Recent backend log records (newest first):')
  if (entries.length === 0) lines.push('- none')
  for (const entry of entries) {
    lines.push(
      `- ${entry.timestamp} ${entry.level.toUpperCase()} ${entry.logger}: ${entry.message}`,
    )
  }
  return lines.join('\n')
}

async function writeClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  if (!ok) throw new Error('Copy command was rejected')
}

function CheckRow({ check }: { check: DiagnosticCheck }) {
  const spec = checkStatus(check.status)
  const { Icon } = spec
  return (
    <li className="border-b border-border px-4 py-3 last:border-b-0">
      <div className="flex items-center gap-3">
        <Icon aria-hidden="true" className={cn('size-icon shrink-0', TONE_TEXT[spec.tone])} />
        <span className="min-w-0 flex-1 text-body font-medium text-text">{check.label}</span>
        <Badge tone={spec.tone} className="shrink-0">
          {spec.label}
        </Badge>
      </div>
      {check.message ? <p className="pl-8 text-label text-text-muted">{check.message}</p> : null}
    </li>
  )
}

function ErrorRow({ entry }: { entry: DiagnosticLogEntry }) {
  return (
    <tr className="h-row border-b border-border align-top last:border-b-0 hover:bg-surface-muted">
      <td className="whitespace-nowrap px-3 py-2 text-label text-text-muted">
        <RelativeTime iso={entry.timestamp} />
      </td>
      <td className="px-3 py-2">
        <Badge tone={levelTone(entry.level)}>{levelLabel(entry.level)}</Badge>
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
  const queryClient = useQueryClient()
  const clear = useMutation({
    mutationFn: clearRecentErrors,
    onSuccess: () =>
      queryClient.setQueryData<DiagnosticsResponse>(queryKeys.diagnostics, (old) =>
        old ? { ...old, recent_errors: [] } : old,
      ),
  })
  const [checksOpen, setChecksOpen] = useState(false)
  const checksPanelId = useId()
  const [filter, setFilter] = useState<LevelFilter>('all')
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  useEffect(() => {
    if (copyState !== 'copied') return
    const timer = window.setTimeout(() => setCopyState('idle'), 2000)
    return () => window.clearTimeout(timer)
  }, [copyState])

  const allEntries = data?.recent_errors ?? []
  const visibleEntries = allEntries.filter((entry) => matchesFilter(entry, filter))
  const filterCounts: Record<LevelFilter, number> = {
    all: allEntries.length,
    warning: allEntries.filter((entry) => matchesFilter(entry, 'warning')).length,
    error: allEntries.filter((entry) => matchesFilter(entry, 'error')).length,
  }

  async function copyReport() {
    if (!data) return
    try {
      await writeClipboard(buildReport(data, visibleEntries))
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

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
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => void copyReport()} disabled={!data}>
            {copyState === 'copied' ? (
              <Check aria-hidden="true" className="size-icon" />
            ) : (
              <Copy aria-hidden="true" className="size-icon" />
            )}
            {copyState === 'copied' ? 'Copied' : 'Copy for fixing'}
          </Button>
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
      </div>
      {copyState === 'failed' ? (
        <p role="alert" className="text-label text-st-error-fg">
          Could not copy to the clipboard. Select the text and copy it manually.
        </p>
      ) : null}

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
            <button
              type="button"
              aria-expanded={checksOpen}
              aria-controls={checksPanelId}
              onClick={() => setChecksOpen((value) => !value)}
              className="flex w-full items-center justify-between gap-3 rounded text-left transition hover:bg-surface-muted"
            >
              <h2 id="checks-heading" className="text-section text-text">
                Checks
              </h2>
              <span className="flex items-center gap-3">
                {counts ? (
                  <span className="text-label tabular-nums text-text-muted">
                    {counts.ok} OK, {pluralize(counts.warning, 'warning')},{' '}
                    {pluralize(counts.error, 'error')}
                  </span>
                ) : null}
                <ChevronDown
                  aria-hidden="true"
                  className={cn(
                    'size-icon shrink-0 text-text-muted transition',
                    checksOpen && 'rotate-180',
                  )}
                />
              </span>
            </button>
            <div
              id={checksPanelId}
              hidden={!checksOpen}
              className="rounded border border-border bg-surface"
            >
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
            {data && data.recent_errors.length > 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div role="group" aria-label="Filter by level" className="flex flex-wrap gap-2">
                  {FILTERS.map((item) => {
                    const selected = filter === item.value
                    return (
                      <button
                        type="button"
                        key={item.value}
                        aria-pressed={selected}
                        onClick={() => setFilter(item.value)}
                        className={cn(
                          'rounded border border-border px-3 py-1 text-label transition',
                          selected
                            ? 'bg-surface-muted text-text ring-2 ring-accent'
                            : 'hover:bg-surface-muted',
                        )}
                      >
                        {item.label} ({filterCounts[item.value]})
                      </button>
                    )
                  })}
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => clear.mutate()}
                  disabled={clear.isPending}
                >
                  <Trash2 aria-hidden="true" className="size-icon" />
                  {clear.isPending ? 'Clearing…' : 'Clear log'}
                </Button>
              </div>
            ) : null}
            {clear.isError ? (
              <p role="alert" className="text-label text-st-error-fg">
                Could not clear the log: {errorMessage(clear.error)}
              </p>
            ) : null}
            {data && data.recent_errors.length === 0 ? (
              <EmptyState title="No warnings or errors logged recently." />
            ) : data && visibleEntries.length === 0 ? (
              <EmptyState title="No log records match this filter." />
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
                      visibleEntries.map((entry, index) => (
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
