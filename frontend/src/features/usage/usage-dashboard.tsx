import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  BarChart2,
  DollarSign,
  RefreshCw,
  Settings,
  TrendingUp,
  Wallet,
} from 'lucide-react'
import { useState } from 'react'

import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card } from '../../components/ui/card'
import { getUsageAggregate, type UsageAggregateResponse, type UsageQueryParams } from '../../lib/api'
import { cn } from '../../lib/utils'

interface UsageDashboardProps {
  className?: string
}

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

function StatsCard({
  icon: Icon,
  title,
  value,
  description,
  trend,
}: {
  icon: React.ComponentType<{ className?: string; size?: number }>
  title: string
  value: string
  description?: string
  trend?: string
}) {
  return (
    <Card className="overflow-hidden p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">{title}</p>
          <p className="mt-2 text-3xl font-semibold tracking-[-0.04em] text-white">{value}</p>
          {description ? <p className="mt-1 text-sm text-zinc-400">{description}</p> : null}
          {trend ? (
            <p className="mt-2 flex items-center gap-1 text-sm text-emerald-400">
              <TrendingUp className="size-3.5" />
              {trend}
            </p>
          ) : null}
        </div>
        <div className="rounded-2xl border border-white/8 bg-white/[0.04] p-3 text-white">
          <Icon className="size-5" />
        </div>
      </div>
    </Card>
  )
}

function ProviderTable({ providers }: { providers: UsageAggregateResponse['by_provider'] }) {
  if (!providers.length) {
    return (
      <Card className="p-5 text-center">
        <p className="text-zinc-400">No provider data available</p>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-white/8 text-left text-sm">
          <thead className="bg-black/10 text-zinc-500">
            <tr>
              {['Provider', 'Models', 'Requests', 'Tokens', 'Cost'].map((heading) => (
                <th key={heading} className="px-4 py-4 font-medium">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/6">
            {providers.map((p: UsageAggregateResponse['by_provider'][0]) => (
              <tr key={p.provider} className="align-top">
                <td className="px-4 py-4">
                  <p className="font-medium text-white capitalize">{p.provider}</p>
                </td>
                <td className="px-4 py-4 text-zinc-200">
                  <div className="flex flex-wrap gap-1">
                    {p.models_used.map((model: string) => (
                      <Badge key={model} className="text-xs">
                        {model}
                      </Badge>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-4 text-zinc-200">{formatNumber(p.total_requests)}</td>
                <td className="px-4 py-4 text-zinc-200">{formatNumber(p.total_tokens)}</td>
                <td className="px-4 py-4 text-zinc-200">{formatCost(p.total_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function OperationTable({ operations }: { operations: UsageAggregateResponse['by_operation'] }) {
  if (!operations.length) {
    return (
      <Card className="p-5 text-center">
        <p className="text-zinc-400">No operation data available</p>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-white/8 text-left text-sm">
          <thead className="bg-black/10 text-zinc-500">
            <tr>
              {['Operation', 'Requests', 'Tokens', 'Cost', 'Avg tokens/req'].map((heading) => (
                <th key={heading} className="px-4 py-4 font-medium">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/6">
            {operations.map((o: UsageAggregateResponse['by_operation'][0]) => (
              <tr key={o.operation} className="align-top">
                <td className="px-4 py-4">
                  <p className="font-medium text-white capitalize">{o.operation.replaceAll('_', ' ')}</p>
                </td>
                <td className="px-4 py-4 text-zinc-200">{formatNumber(o.total_requests)}</td>
                <td className="px-4 py-4 text-zinc-200">{formatNumber(o.total_tokens)}</td>
                <td className="px-4 py-4 text-zinc-200">{formatCost(o.total_cost_usd)}</td>
                <td className="px-4 py-4 text-zinc-200">{formatNumber(o.avg_tokens_per_request)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

function CacheStats({ cache }: { cache: UsageAggregateResponse['cache_stats'] }) {
  if (!cache) {
    return (
      <Card className="p-5 text-center">
        <p className="text-zinc-400">No cache data available</p>
      </Card>
    )
  }

  return (
    <Card className="p-5">
      <div className="grid gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-emerald-400/12 bg-emerald-400/[0.04] p-4">
          <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">Cache hits</p>
          <p className="mt-2 text-2xl font-semibold text-emerald-400">{formatNumber(cache.total_hits)}</p>
        </div>
        <div className="rounded-2xl border border-amber-400/12 bg-amber-400/[0.04] p-4">
          <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">Cache misses</p>
          <p className="mt-2 text-2xl font-semibold text-amber-400">{formatNumber(cache.total_misses)}</p>
        </div>
        <div className="rounded-2xl border border-sky-400/12 bg-sky-400/[0.04] p-4">
          <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">Hit rate</p>
          <p className="mt-2 text-2xl font-semibold text-sky-400">{formatPercent(cache.hit_rate)}</p>
        </div>
      </div>

      {cache.by_kind && Object.keys(cache.by_kind).length > 0 && (
        <div className="mt-6">
          <p className="mb-3 text-sm font-medium text-zinc-300">By kind</p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(Object.entries(cache.by_kind) as [string, [number, number]][]).map(([kind, value]) => {
              const [hits, misses] = value
              return (
                <div key={kind} className="rounded-xl border border-white/8 bg-black/10 p-3">
                  <p className="text-xs text-zinc-400 capitalize">{kind.replaceAll('_', ' ')}</p>
                  <div className="mt-1 flex items-center justify-between text-sm">
                    <span className="text-emerald-400">Hits: {formatNumber(hits)}</span>
                    <span className="text-amber-400">Misses: {formatNumber(misses)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Card>
  )
}

export function UsageDashboard({ className }: UsageDashboardProps) {
  const [params, setParams] = useState<UsageQueryParams>({})
  const [refreshKey, setRefreshKey] = useState(0)

  const { data, isLoading, error } = useQuery({
    queryKey: ['usage', params, refreshKey],
    queryFn: () => getUsageAggregate(params),
    refetchInterval: 30000,
  })

  const handleRefresh = () => setRefreshKey((k) => k + 1)

  return (
    <section id="usage" className={cn('space-y-8', className)}>
      <motion.div
        animate={{ opacity: 1, y: 0 }}
        initial={{ opacity: 0, y: 20 }}
        transition={{ duration: 0.4 }}
      >
        <Card className="mx-auto max-w-6xl overflow-hidden border-white/8 bg-[linear-gradient(180deg,rgba(24,27,33,0.96),rgba(16,18,22,0.92))] p-6 md:p-8">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <h2 className="text-2xl font-semibold tracking-[-0.03em] text-white">Usage &amp; Cost Dashboard</h2>
              <p className="mt-1 text-sm text-zinc-400">
                Track API usage, token consumption, cache performance, and estimated costs across providers.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <label className="text-xs text-zinc-500">Provider</label>
                <select
                  className="h-10 w-40 rounded-xl border border-white/8 bg-white/[0.04] px-3 text-sm text-white outline-none focus:border-white/15"
                  value={params.provider ?? ''}
                  onChange={(e) => setParams({ ...params, provider: e.target.value || undefined })}
                >
                  <option value="">All providers</option>
                  <option value="openai">OpenAI</option>
                  <option value="gemini">Gemini</option>
                  <option value="anthropic">Anthropic</option>
                </select>
              </div>
              <div className="flex items-center gap-2">
                <label className="text-xs text-zinc-500">Operation</label>
                <select
                  className="h-10 w-40 rounded-xl border border-white/8 bg-white/[0.04] px-3 text-sm text-white outline-none focus:border-white/15"
                  value={params.operation ?? ''}
                  onChange={(e) => setParams({ ...params, operation: e.target.value || undefined })}
                >
                  <option value="">All operations</option>
                  <option value="summarization">Summarization</option>
                  <option value="transcription">Transcription</option>
                  <option value="quiz_generation">Quiz generation</option>
                  <option value="comparison">Comparison</option>
                  <option value="assignment">Assignment</option>
                </select>
              </div>
              <Button className="gap-2" onClick={handleRefresh} disabled={isLoading} variant="secondary">
                <RefreshCw className={cn('size-4', isLoading && 'animate-spin')} />
                Refresh
              </Button>
            </div>
          </div>
        </Card>
      </motion.div>

      {error ? (
        <Card className="border-red-400/20 bg-red-500/8 p-4 text-sm text-red-100">
          Failed to load usage data: {error.message}
        </Card>
      ) : null}

      {isLoading && !data ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-24 animate-pulse rounded-[24px] border border-white/8 bg-white/[0.04]" />
          ))}
        </div>
      ) : null}

      {!isLoading && data && (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatsCard
              icon={Wallet}
              title="Total cost"
              value={formatCost(data.total_cost_usd)}
              description={`${data.total_requests} requests • ${formatNumber(data.total_tokens)} tokens`}
            />
            <StatsCard
              icon={BarChart2}
              title="Total requests"
              value={formatNumber(data.total_requests)}
              description={`Input: ${formatNumber(data.total_input_tokens)} • Output: ${formatNumber(data.total_output_tokens)}`}
            />
            <StatsCard
              icon={DollarSign}
              title="Avg cost / request"
              value={formatCost(data.total_requests ? data.total_cost_usd / data.total_requests : 0)}
            />
            <StatsCard
              icon={Settings}
              title="Cache hit rate"
              value={formatPercent(data.cache_hit_rate)}
              description="Reduces redundant API calls"
            />
          </div>

          <div className="grid gap-6 lg:grid-cols-[0.55fr,1.45fr]">
            <Card className="overflow-hidden p-5">
              <h3 className="mb-4 text-lg font-semibold text-white">By provider</h3>
              <ProviderTable providers={data.by_provider} />
            </Card>

            <Card className="overflow-hidden p-5">
              <h3 className="mb-4 text-lg font-semibold text-white">By operation</h3>
              <OperationTable operations={data.by_operation} />
            </Card>

            <Card className="overflow-hidden p-5 lg:col-span-2">
              <h3 className="mb-4 text-lg font-semibold text-white">Cache performance</h3>
              <CacheStats cache={data.cache_stats} />
            </Card>

            <Card className="overflow-hidden p-5 lg:col-span-2">
              <h3 className="mb-4 text-lg font-semibold text-white">Time range</h3>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border border-white/8 bg-black/10 p-4">
                  <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">Since</p>
                  <p className="mt-2 text-sm text-zinc-200">{data.time_range?.since ?? 'All time'}</p>
                </div>
                <div className="rounded-xl border border-white/8 bg-black/10 p-4">
                  <p className="text-[11px] uppercase tracking-[0.2em] text-zinc-500">Until</p>
                  <p className="mt-2 text-sm text-zinc-200">{data.time_range?.until ?? 'Now'}</p>
                </div>
              </div>
            </Card>
          </div>
        </>
      )}
    </section>
  )
}