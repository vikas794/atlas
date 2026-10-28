import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { ErrorState, SkeletonBar } from '../../components/shared/state-panels'
import {
  getLlmKeys,
  getLlmProviders,
  getUsageAggregate,
  type UsageAggregateResponse,
  type UsageQueryParams,
} from '../../lib/api'
import { queryKeys } from '../../lib/query-keys'
import { cn } from '../../lib/utils'

interface UsageDashboardProps {
  className?: string
}

/** Operations Atlas actually records. Provider options come from the enabled keys. */
const OPERATION_OPTIONS = [{ value: 'generate_notes', label: 'Generate notes' }]

function formatNumber(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function formatCost(value: number) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  }).format(value)
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

/** Flat stat tile: label, value, optional detail. One level of border, no nested cards. */
function Stat({
  title,
  value,
  description,
}: {
  title: string
  value: string
  description?: string
}) {
  return (
    <div className="panel card-lift p-5">
      <p className="text-label text-text-muted">{title}</p>
      <p className="mt-2 font-display text-[32px] font-semibold leading-9 tabular-nums text-text">
        {value}
      </p>
      {description ? <p className="mt-1 text-label text-text-muted">{description}</p> : null}
    </div>
  )
}

function TableShell({ empty, children }: { empty?: string; children?: React.ReactNode }) {
  if (empty) {
    return <p className="panel p-4 text-center text-body text-text-muted">{empty}</p>
  }
  return (
    <div className="overflow-x-auto panel">
      <table className="min-w-full text-left text-body">{children}</table>
    </div>
  )
}

function Head({ columns }: { columns: string[] }) {
  return (
    <thead className="bg-surface-muted text-label text-text">
      <tr className="h-row">
        {columns.map((heading) => (
          <th key={heading} scope="col" className="px-3">
            {heading}
          </th>
        ))}
      </tr>
    </thead>
  )
}

function ProviderTable({ providers }: { providers: UsageAggregateResponse['by_provider'] }) {
  if (!providers.length) return <TableShell empty="No provider data yet." />

  return (
    <TableShell>
      <Head columns={['Provider', 'Models', 'Requests', 'Tokens', 'Cost']} />
      <tbody>
        {providers.map((p) => (
          <tr
            key={p.provider}
            className="h-row border-t border-border align-middle hover:bg-surface-muted"
          >
            <td className="px-3 py-1 capitalize text-text">{p.provider}</td>
            <td className="px-3 py-1">
              <div className="flex flex-wrap gap-1">
                {p.models_used.map((model) => (
                  <Badge key={model}>{model}</Badge>
                ))}
              </div>
            </td>
            <td className="px-3 py-1 tabular-nums text-text">{formatNumber(p.total_requests)}</td>
            <td className="px-3 py-1 tabular-nums text-text">{formatNumber(p.total_tokens)}</td>
            <td className="px-3 py-1 tabular-nums text-text">{formatCost(p.total_cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  )
}

function OperationTable({ operations }: { operations: UsageAggregateResponse['by_operation'] }) {
  if (!operations.length) return <TableShell empty="No operation data yet." />

  return (
    <TableShell>
      <Head columns={['Operation', 'Requests', 'Tokens', 'Cost', 'Avg tokens per request']} />
      <tbody>
        {operations.map((o) => (
          <tr key={o.operation} className="h-row border-t border-border hover:bg-surface-muted">
            <td className="px-3 py-1 capitalize text-text">{o.operation.replaceAll('_', ' ')}</td>
            <td className="px-3 py-1 tabular-nums text-text">{formatNumber(o.total_requests)}</td>
            <td className="px-3 py-1 tabular-nums text-text">{formatNumber(o.total_tokens)}</td>
            <td className="px-3 py-1 tabular-nums text-text">{formatCost(o.total_cost_usd)}</td>
            <td className="px-3 py-1 tabular-nums text-text">
              {formatNumber(o.avg_tokens_per_request)}
            </td>
          </tr>
        ))}
      </tbody>
    </TableShell>
  )
}

export function UsageDashboard({ className }: UsageDashboardProps) {
  const [params, setParams] = useState<UsageQueryParams>({})

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ['usage', params],
    queryFn: () => getUsageAggregate(params),
    refetchInterval: 30000,
  })

  const keysQuery = useQuery({ queryKey: queryKeys.llmKeys, queryFn: getLlmKeys })
  const providersQuery = useQuery({ queryKey: queryKeys.llmProviders, queryFn: getLlmProviders })

  const providerOptions = useMemo(() => {
    const labels = new Map(providersQuery.data?.providers.map((p) => [p.name, p.label]))
    const names = new Set(
      (keysQuery.data?.keys ?? []).filter((k) => k.enabled).map((k) => k.provider),
    )
    return [...names]
      .map((value) => ({ value, label: labels.get(value) ?? value }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [keysQuery.data, providersQuery.data])

  // Drop a stale selection once its provider no longer has an enabled key.
  useEffect(() => {
    if (!keysQuery.data || !params.provider) return
    if (!providerOptions.some((o) => o.value === params.provider)) {
      setParams((prev) => ({ ...prev, provider: undefined }))
    }
  }, [keysQuery.data, providerOptions, params.provider])

  const cache = data?.cache_stats

  return (
    <section id="usage" className={cn('space-y-4', className)}>
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="text-section text-text">Usage and cost</h2>
          <p className="mt-1 text-body text-text-muted">
            API requests, tokens, cache performance and estimated cost.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-label text-text-muted">
            Provider
            <select
              className="input w-40"
              value={params.provider ?? ''}
              onChange={(e) => setParams({ ...params, provider: e.target.value || undefined })}
            >
              <option value="">All providers</option>
              {providerOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-label text-text-muted">
            Operation
            <select
              className="input w-40"
              value={params.operation ?? ''}
              onChange={(e) => setParams({ ...params, operation: e.target.value || undefined })}
            >
              <option value="">All operations</option>
              {OPERATION_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <Button variant="secondary" onClick={() => void refetch()} disabled={isFetching}>
            <RefreshCw aria-hidden="true" className={cn('size-icon', isFetching && 'atlas-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {error ? (
        <ErrorState
          title="Could not load usage data"
          message={error.message}
          onRetry={() => void refetch()}
        />
      ) : null}

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4" aria-busy="true">
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="space-y-2 panel p-4">
              <SkeletonBar className="w-16" />
              <SkeletonBar className="h-6 w-24" />
            </div>
          ))}
        </div>
      ) : null}

      {data && (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Stat
              title="Total cost"
              value={formatCost(data.total_cost_usd)}
              description={`${data.total_requests} requests, ${formatNumber(data.total_tokens)} tokens`}
            />
            <Stat
              title="Total requests"
              value={formatNumber(data.total_requests)}
              description={`Input ${formatNumber(data.total_input_tokens)}, output ${formatNumber(data.total_output_tokens)}`}
            />
            <Stat
              title="Average cost per request"
              value={formatCost(
                data.total_requests ? data.total_cost_usd / data.total_requests : 0,
              )}
            />
            <Stat
              title="Cache hit rate"
              value={formatPercent(data.cache_hit_rate)}
              description={
                cache
                  ? `${formatNumber(cache.total_hits)} hits, ${formatNumber(cache.total_misses)} misses`
                  : undefined
              }
            />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <div className="space-y-2">
              <h3 className="text-section text-text">By provider</h3>
              <ProviderTable providers={data.by_provider} />
            </div>
            <div className="space-y-2">
              <h3 className="text-section text-text">By operation</h3>
              <OperationTable operations={data.by_operation} />
            </div>
          </div>

          <p className="text-label text-text-muted">
            Range: {data.time_range?.since ?? 'all time'} to {data.time_range?.until ?? 'now'}
          </p>
        </>
      )}
    </section>
  )
}
