import { useQuery } from '@tanstack/react-query'
import { Fragment, useState } from 'react'

import { ErrorState, SkeletonBar } from '../../components/shared/state-panels'
import { getKeyAnalytics } from '../../lib/api'
import { queryKeys } from '../../lib/query-keys'
import type { KeyAnalyticsRange } from '../../lib/types'
import { formatCost, formatNumber } from './usage-format'
import { Head, TableShell } from './usage-ui'

const RANGE_OPTIONS: { value: KeyAnalyticsRange; label: string }[] = [
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: 'all', label: 'All time' },
]

function percent(value: number) {
  return `${value.toFixed(1)}%`
}

/** Per-key success and error counts, cost per model and error categories (from the attempt log). */
export function KeyAnalytics() {
  const [range, setRange] = useState<KeyAnalyticsRange>('7d')
  const [expanded, setExpanded] = useState<string | null>(null)
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: queryKeys.usageKeys(range),
    queryFn: () => getKeyAnalytics(range),
    refetchInterval: 30000,
  })

  return (
    <section id="key-analytics" aria-labelledby="key-analytics-title" className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 id="key-analytics-title" className="text-section text-text">API key analytics</h2>
          <p className="mt-1 text-body text-text-muted">
            Success, errors, latency and estimated cost for each key and model.
          </p>
        </div>
        <label className="flex items-center gap-2 text-label text-text-muted">
          Range
          <select
            className="input w-40"
            value={range}
            onChange={(e) => setRange(e.target.value as KeyAnalyticsRange)}
          >
            {RANGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <ErrorState
          title="Could not load key analytics"
          message={error.message}
          onRetry={() => void refetch()}
        />
      ) : null}

      {isLoading ? (
        <div className="space-y-2 panel p-4" aria-busy="true">
          <SkeletonBar className="w-32" />
          <SkeletonBar className="h-6 w-48" />
        </div>
      ) : null}

      {data ? (
        <>
          <p className="text-label text-text-muted">
            {data.total_requests} provider calls, {data.total_errors} failed in this range.
          </p>

          <div className="space-y-2">
            <h3 className="text-section text-text">By API key</h3>
            {data.by_key.length ? (
              <TableShell>
                <Head
                  columns={[
                    'Key',
                    'Provider',
                    'Calls',
                    'Success',
                    'Errors',
                    'Success rate',
                    'Avg latency',
                    'Tokens',
                    'Cost',
                  ]}
                />
                <tbody>
                  {data.by_key.map((row) => (
                    <tr
                      key={row.key_id ?? 'none'}
                      className="h-row border-t border-border hover:bg-surface-muted"
                    >
                      <td className="px-3 py-1 text-text">
                        {row.label || (row.key_id != null ? `Key #${row.key_id}` : 'No key')}
                      </td>
                      <td className="px-3 py-1 capitalize text-text">{row.provider}</td>
                      <td className="px-3 py-1 tabular-nums text-text">{row.requests}</td>
                      <td className="px-3 py-1 tabular-nums text-text">{row.successes}</td>
                      <td className="px-3 py-1 tabular-nums text-text">{row.errors}</td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {percent(row.success_rate)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">{row.avg_latency_ms} ms</td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatNumber(row.input_tokens + row.output_tokens)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatCost(row.cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </TableShell>
            ) : (
              <TableShell empty="No key activity in this range yet." />
            )}
          </div>

          <div className="space-y-2">
            <h3 className="text-section text-text">Cost per model</h3>
            {data.by_model.length ? (
              <TableShell>
                <Head
                  columns={[
                    'Model',
                    'Provider',
                    'Calls',
                    'Success rate',
                    'Input tokens',
                    'Output tokens',
                    'Total tokens',
                    'Cost',
                    'Avg cost per call',
                    'Share',
                  ]}
                />
                <tbody>
                  {data.by_model.map((row) => (
                    <tr
                      key={`${row.provider}/${row.model}`}
                      className="h-row border-t border-border hover:bg-surface-muted"
                    >
                      <td className="px-3 py-1 text-text">{row.model}</td>
                      <td className="px-3 py-1 capitalize text-text">{row.provider}</td>
                      <td className="px-3 py-1 tabular-nums text-text">{row.requests}</td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {percent(row.success_rate)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatNumber(row.input_tokens)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatNumber(row.output_tokens)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatNumber(row.total_tokens)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatCost(row.cost_usd)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">
                        {formatCost(row.avg_cost_usd)}
                      </td>
                      <td className="px-3 py-1 tabular-nums text-text">{percent(row.cost_share)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="h-row border-t border-border bg-surface-muted text-text">
                    <th scope="row" className="px-3 text-left" colSpan={2}>
                      Total
                    </th>
                    <td className="px-3 tabular-nums">{data.total_requests}</td>
                    <td colSpan={4} />
                    <td className="px-3 tabular-nums">{formatCost(data.total_cost_usd)}</td>
                    <td colSpan={2} />
                  </tr>
                </tfoot>
              </TableShell>
            ) : (
              <TableShell empty="No model activity in this range yet." />
            )}
          </div>

          {data.errors.length ? (
            <div className="space-y-2">
              <h3 className="text-section text-text">Errors by category</h3>
              <TableShell>
                <Head columns={['Category', 'Count']} />
                <tbody>
                  {data.errors.map((row) => {
                    const isOpen = expanded === row.category
                    return (
                      <Fragment key={row.category}>
                        <tr className="h-row border-t border-border hover:bg-surface-muted">
                          <td className="px-3 py-1 text-text">
                            <button
                              type="button"
                              className="flex items-center gap-2 text-left"
                              aria-expanded={isOpen}
                              onClick={() => setExpanded(isOpen ? null : row.category)}
                            >
                              <span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
                              {row.category.replaceAll('_', ' ')}
                            </button>
                          </td>
                          <td className="px-3 py-1 tabular-nums text-text">{row.count}</td>
                        </tr>
                        {isOpen ? (
                          <tr className="border-t border-border bg-surface-muted">
                            <td colSpan={2} className="px-3 py-2">
                              <TableShell>
                                <Head
                                  columns={[
                                    'Model',
                                    'Provider',
                                    'Label',
                                    'Operation',
                                    'Count',
                                    'Last seen',
                                    'Last message',
                                  ]}
                                />
                                <tbody>
                                  {row.details.map((d) => (
                                    <tr
                                      key={`${d.provider}/${d.model}/${d.operation}/${d.key_label}`}
                                      className="h-row border-t border-border"
                                    >
                                      <td className="px-3 py-1 text-text">{d.model}</td>
                                      <td className="px-3 py-1 capitalize text-text">
                                        {d.provider}
                                      </td>
                                      <td className="px-3 py-1 text-text">{d.key_label || '-'}</td>
                                      <td className="px-3 py-1 text-text">
                                        {d.operation.replaceAll('_', ' ')}
                                      </td>
                                      <td className="px-3 py-1 tabular-nums text-text">
                                        {d.count}
                                      </td>
                                      <td className="px-3 py-1 text-text">
                                        {new Date(d.last_at).toLocaleString()}
                                      </td>
                                      <td className="px-3 py-1 break-words text-text-muted">
                                        {d.last_message ?? 'Not recorded'}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </TableShell>
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    )
                  })}
                </tbody>
              </TableShell>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
