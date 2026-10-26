import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, KeyRound, Layers, Plus, Power, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useBlocker } from 'react-router-dom'

import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { PageHeader } from '../../components/shared/page-header'
import { StatCard } from '../../components/shared/stat-card'
import { EmptyState, ErrorState, SkeletonTableRows } from '../../components/shared/state-panels'
import { StagedBar } from '../../components/shared/staged-bar'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import { buttonClasses } from '../../components/ui/button-styles'
import {
  deleteLlmModel,
  getFallback,
  getRateLimitUsage,
  saveFallback,
  testLlmModel,
} from '../../lib/api'
import { errorMessage, failureMessage } from '../../lib/errors'
import {
  applyStaged,
  chainChanged,
  setEnabled,
  toStaged,
  toUpdates,
  type StagedEntry,
} from '../../lib/model-chain'
import { queryKeys } from '../../lib/query-keys'
import { useReveal } from '../../lib/use-reveal'
import type { FallbackEntry, RateLimitRow } from '../../lib/types'
import { ModelDialog } from './model-dialog'
import { ModelTable } from './model-table'

/** Polled so quota badges follow live usage while the page is open. */
const USAGE_POLL_MS = 15_000

/**
 * Models (design.md 6.4): the ordered fallback chain notes generation walks. Reorder and enable
 * edits are staged locally and saved together with the bar at the bottom.
 */
export function ModelsScreen() {
  const [staged, setStaged] = useState<StagedEntry[] | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<FallbackEntry | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<FallbackEntry | null>(null)

  const queryClient = useQueryClient()
  const { toast } = useToast()

  const chain = useQuery({ queryKey: queryKeys.llmFallback, queryFn: getFallback })
  const usage = useQuery({
    queryKey: queryKeys.llmRateUsage,
    queryFn: getRateLimitUsage,
    refetchInterval: USAGE_POLL_MS,
    placeholderData: keepPreviousData,
    retry: false,
  })

  const saved = useMemo(() => chain.data?.entries ?? [], [chain.data])
  const entries = useMemo(() => applyStaged(saved, staged), [saved, staged])
  const dirty = staged !== null && chainChanged(saved, entries)
  const rates = useMemo(
    () => new Map<number, RateLimitRow>((usage.data?.rows ?? []).map((row) => [row.model_pk, row])),
    [usage.data],
  )

  const stats = useMemo(
    () => ({
      enabled: entries.filter((entry) => entry.enabled).length,
      ready: entries.filter((entry) => entry.enabled && entry.usable_key_count > 0).length,
      noKeys: entries.filter((entry) => entry.key_count === 0).length,
    }),
    [entries],
  )
  const servingOrder = useMemo(
    () =>
      entries
        .filter((entry) => entry.enabled && entry.usable_key_count > 0)
        .map((entry) => entry.display_name || entry.model_id),
    [entries],
  )
  const scope = useReveal<HTMLDivElement>([entries.length])

  const stage = (next: FallbackEntry[]) => setStaged(toStaged(next))

  const save = useMutation({
    mutationFn: () => saveFallback(toUpdates(entries)),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.llmFallback, data)
      setStaged(null)
      toast('Model chain saved.')
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmRateUsage })
    },
    onError: (error) => toast(failureMessage('Could not save the model chain', error), 'error'),
  })

  const test = useMutation({
    mutationFn: (entry: FallbackEntry) => testLlmModel(entry.id),
    onSuccess: (result, entry) =>
      result.success
        ? toast(`${entry.display_name || entry.model_id} answered in ${result.latency_ms} ms.`)
        : toast(`Test failed: ${result.error ?? 'unknown error'}`, 'error'),
    onError: (error) => toast(failureMessage('Could not test the model', error), 'error'),
  })

  const remove = useMutation({
    mutationFn: (entry: FallbackEntry) => deleteLlmModel(entry.id),
    onSuccess: () => {
      toast('Model deleted.')
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmRateUsage })
    },
    onError: (error) => toast(failureMessage('Could not delete the model', error), 'error'),
  })

  // Leaving with staged edits asks first (in-app navigation and closing the tab).
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  const leavingRef = useRef(false)
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const openAdd = () => {
    setEditTarget(null)
    setDialogOpen(true)
  }
  const openEdit = (entry: FallbackEntry) => {
    setEditTarget(entry)
    setDialogOpen(true)
  }

  const isEmpty = chain.isSuccess && entries.length === 0

  return (
    <div ref={scope}>
      <PageHeader
        title="Models"
        description="Notes generation tries these in order. If a model has no usable key or fails, the next one takes over."
        actions={
          <>
            <Link to="/keys" className={buttonClasses({ variant: 'secondary' })}>
              <KeyRound aria-hidden="true" className="size-icon" />
              Keys
            </Link>
            <Button onClick={openAdd}>
              <Plus aria-hidden="true" className="size-icon" />
              Add model
            </Button>
          </>
        }
      />

      {entries.length > 0 ? (
        <>
          <div className="mt-6 grid grid-flow-dense grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="In the chain"
              value={entries.length}
              hint="Tried top to bottom"
              icon={Layers}
            />
            <StatCard
              label="Enabled"
              value={stats.enabled}
              hint="Eligible to serve"
              icon={Power}
              tone="success"
            />
            <StatCard
              label="Ready now"
              value={stats.ready}
              hint="Enabled with a usable key"
              icon={Zap}
              tone="progress"
            />
            <StatCard
              label="Missing keys"
              value={stats.noKeys}
              hint="Add a key to bring them online"
              icon={KeyRound}
              tone={stats.noKeys > 0 ? 'error' : 'neutral'}
            />
          </div>

          {servingOrder.length > 0 ? (
            <div data-reveal className="panel mt-4 flex flex-wrap items-center gap-2 px-5 py-3">
              <span className="mr-1 text-label text-text-muted">Serving order</span>
              {servingOrder.map((name, index) => (
                <span key={`${name}-${index}`} className="inline-flex items-center gap-2">
                  {index > 0 ? (
                    <ArrowRight aria-hidden="true" className="size-icon text-text-muted" />
                  ) : null}
                  <span
                    className={
                      index === 0
                        ? 'rounded-pill bg-accent px-3 py-1 text-label text-on-accent'
                        : 'rounded-pill bg-surface-muted px-3 py-1 text-label text-text'
                    }
                  >
                    {name}
                  </span>
                </span>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      <div className="mt-6">
        {chain.isError ? (
          <ErrorState
            title="Could not load the models"
            message={errorMessage(chain.error)}
            onRetry={() => void chain.refetch()}
          />
        ) : chain.isPending ? (
          <div className="panel overflow-hidden">
            <table className="w-full">
              <tbody>
                <SkeletonTableRows
                  rows={5}
                  widths={['w-4', 'w-6', 'w-48', 'w-10', 'w-16', 'w-12', 'w-8', 'w-4']}
                />
              </tbody>
            </table>
          </div>
        ) : isEmpty ? (
          <EmptyState title="No models yet. Add one to start generating notes.">
            <Button className="mt-3" onClick={openAdd}>
              <Plus aria-hidden="true" className="size-icon" />
              Add model
            </Button>
          </EmptyState>
        ) : (
          <ModelTable
            entries={entries}
            rates={rates}
            onReorder={stage}
            onToggle={(id, enabled) => stage(setEnabled(entries, id, enabled))}
            onEdit={openEdit}
            onTest={(entry) => test.mutate(entry)}
            onDelete={setDeleteTarget}
          />
        )}
      </div>

      {dirty ? (
        <StagedBar
          onDiscard={() => setStaged(null)}
          onSave={() => save.mutate()}
          isSaving={save.isPending}
        />
      ) : null}

      <ModelDialog open={dialogOpen} onOpenChange={setDialogOpen} model={editTarget} />

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
        title="Delete model"
        description={
          deleteTarget
            ? `Remove ${deleteTarget.display_name || deleteTarget.model_id} (${deleteTarget.provider}) from the chain? Notes already generated with it are kept.`
            : ''
        }
        confirmLabel="Delete model"
        isLoading={remove.isPending}
        onConfirm={() => (deleteTarget ? remove.mutateAsync(deleteTarget) : undefined)}
      />

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => {
          if (open) return
          // The confirm button closes the dialog after proceeding; only Stay/Escape should reset.
          if (leavingRef.current) {
            leavingRef.current = false
            return
          }
          if (blocker.state === 'blocked') blocker.reset()
        }}
        title="Discard unsaved changes"
        description="You have unsaved changes to the model chain. Leave this page and discard them?"
        confirmLabel="Discard and leave"
        cancelLabel="Stay"
        onConfirm={() => {
          leavingRef.current = true
          if (blocker.state === 'blocked') blocker.proceed()
        }}
      />
    </div>
  )
}
