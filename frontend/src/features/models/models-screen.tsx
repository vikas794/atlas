import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, KeyRound, Layers, Plus, Power, Search, Zap } from 'lucide-react'
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
  bulkDeleteLlmModels,
  bulkHideLlmModels,
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
  filterEntries,
  listedEntries,
  setEnabled,
  toggleMessage,
  toStaged,
  toUpdates,
  type StagedEntry,
} from '../../lib/model-chain'
import { queryKeys } from '../../lib/query-keys'
import { useReveal } from '../../lib/use-reveal'
import { cn } from '../../lib/utils'
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
  // View only: never part of the staged chain, so it cannot make the page "dirty".
  const [query, setQuery] = useState('')
  const [announcement, setAnnouncement] = useState('')
  // Ticked rows for a bulk Hide / Delete, and whether hidden models are listed (to unhide them).
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [showHidden, setShowHidden] = useState(false)
  // Models of a provider with no enabled key are left out unless this is on.
  const [showNoKey, setShowNoKey] = useState(false)
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false)

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

  // Models of a provider with no enabled key are not listed (Keys tab); models the user hid are
  // left out too unless "Show hidden" is on. Neither kind is counted in the stats.
  const visible = useMemo(
    () => listedEntries(entries, showHidden, showNoKey),
    [entries, showHidden, showNoKey],
  )
  const active = useMemo(() => listedEntries(entries, false), [entries])
  const hiddenCount = useMemo(() => entries.filter((entry) => entry.hidden).length, [entries])
  const stats = useMemo(
    () => ({
      enabled: active.filter((entry) => entry.enabled).length,
      ready: active.filter((entry) => entry.enabled && entry.usable_key_count > 0).length,
      noKey: entries.filter((entry) => !entry.hidden && entry.enabled_key_count === 0).length,
    }),
    [entries, active],
  )
  // Ticks only count while the row is listed (a refetch, search-free filter or key change drops them).
  const selectedEntries = useMemo(
    () => visible.filter((entry) => selected.has(entry.id)),
    [visible, selected],
  )
  const servingOrder = useMemo(
    () =>
      visible
        .filter((entry) => entry.enabled && entry.usable_key_count > 0)
        .map((entry) => entry.display_name || entry.model_id),
    [visible],
  )
  const scope = useReveal<HTMLDivElement>([visible.length])

  const matches = useMemo(() => filterEntries(visible, query).length, [visible, query])

  const stage = (next: FallbackEntry[]) => setStaged(toStaged(next))
  const toggle = (ids: number | number[], enabled: boolean) => {
    const next = setEnabled(entries, ids, enabled)
    stage(next)
    const message = toggleMessage(entries, next, enabled)
    if (message) setAnnouncement(message)
  }

  const save = useMutation({
    mutationFn: () => saveFallback(toUpdates(entries)),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.llmFallback, data)
      setStaged(null)
      setAnnouncement('')
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

  const afterBulk = () => {
    setSelected(new Set())
    void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
    void queryClient.invalidateQueries({ queryKey: queryKeys.llmRateUsage })
  }

  const bulkDelete = useMutation({
    mutationFn: (ids: number[]) => bulkDeleteLlmModels(ids),
    onSuccess: ({ deleted }) => {
      toast(`${deleted} model${deleted === 1 ? '' : 's'} deleted.`)
      afterBulk()
    },
    onError: (error) => toast(failureMessage('Could not delete the models', error), 'error'),
  })

  const hide = useMutation({
    mutationFn: ({ ids, hidden }: { ids: number[]; hidden: boolean }) =>
      bulkHideLlmModels(ids, hidden),
    onSuccess: ({ updated }, { hidden }) => {
      const noun = `${updated} model${updated === 1 ? '' : 's'}`
      toast(
        hidden
          ? `${noun} hidden. "Add all" skips them; turn on Show hidden to bring them back.`
          : `${noun} unhidden, switched off.`,
      )
      setAnnouncement(hidden ? `${noun} hidden` : `${noun} unhidden`)
      afterBulk()
    },
    onError: (error) => toast(failureMessage('Could not update the models', error), 'error'),
  })

  const tick = (ids: number[], checked: boolean) =>
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (checked) next.add(id)
        else next.delete(id)
      }
      return next
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
        description="Notes generation tries these in order. If a model has no usable key or fails, the next one takes over. Disabled models move to the bottom; turning one back on puts it last among enabled models."
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

      <p className="sr-only" aria-live="polite">
        {announcement}
      </p>

      {entries.length > 0 ? (
        <>
          <div className="mt-6 grid grid-flow-dense grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="In the chain"
              value={active.length}
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
              label="Hidden, no key"
              value={stats.noKey}
              hint="Enable a provider key, or tick Show no-key models"
              icon={KeyRound}
              tone={stats.noKey > 0 ? 'error' : 'neutral'}
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
        ) : visible.length === 0 ? (
          <EmptyState
            title={
              hiddenCount > 0
                ? `No models to show. ${hiddenCount} hidden, or enable a key for a provider in the Keys tab.`
                : 'No models to show. Enable a key for a provider in the Keys tab to list its models.'
            }
          >
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {stats.noKey > 0 ? (
                <Button variant="secondary" onClick={() => setShowNoKey(true)}>
                  Show no-key models
                </Button>
              ) : null}
              {hiddenCount > 0 ? (
                <Button variant="secondary" onClick={() => setShowHidden(true)}>
                  Show hidden
                </Button>
              ) : null}
              <Link to="/keys" className={buttonClasses({ variant: 'secondary' })}>
                <KeyRound aria-hidden="true" className="size-icon" />
                Open Keys
              </Link>
            </div>
          </EmptyState>
        ) : (
          <>
            <div
              role="search"
              className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="relative w-full max-w-sm">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-2 size-icon text-text-muted"
                />
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape' && query !== '') {
                      event.preventDefault()
                      setQuery('')
                    }
                  }}
                  aria-label="Search models"
                  placeholder="Search by model, ID or provider"
                  className="input pl-6"
                />
              </div>
              <div className="flex flex-wrap items-center gap-4">
                {stats.noKey > 0 || showNoKey ? (
                  <label className="flex items-center gap-2 text-label text-text">
                    <input
                      type="checkbox"
                      className="size-4 cursor-pointer accent-accent"
                      checked={showNoKey}
                      onChange={(event) => setShowNoKey(event.target.checked)}
                    />
                    Show no-key models ({stats.noKey})
                  </label>
                ) : null}
                {hiddenCount > 0 || showHidden ? (
                  <label className="flex items-center gap-2 text-label text-text">
                    <input
                      type="checkbox"
                      className="size-4 cursor-pointer accent-accent"
                      checked={showHidden}
                      onChange={(event) => setShowHidden(event.target.checked)}
                    />
                    Show hidden ({hiddenCount})
                  </label>
                ) : null}
                <p className="text-label tabular-nums text-text-muted" aria-live="polite">
                  {query.trim() === ''
                    ? `${visible.length} models`
                    : `${matches} of ${visible.length} models. Reordering is off while searching.`}
                </p>
              </div>
            </div>
            <ModelTable
              entries={entries}
              rates={rates}
              onReorder={stage}
              onToggle={(id, enabled) => toggle(id, enabled)}
              onToggleMany={(ids, enabled) => toggle(ids, enabled)}
              onEdit={openEdit}
              onTest={(entry) => test.mutate(entry)}
              onDelete={setDeleteTarget}
              onHide={(entry, hidden) => hide.mutate({ ids: [entry.id], hidden })}
              showHidden={showHidden}
              showNoKey={showNoKey}
              selected={selected}
              onSelect={tick}
              query={query}
              onClearSearch={() => setQuery('')}
            />
          </>
        )}
      </div>

      {selectedEntries.length > 0 ? (
        <div
          role="region"
          aria-label="Selected models"
          className={cn(
            'glass fixed left-1/2 z-40 flex w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 flex-wrap items-center justify-between gap-3 rounded-pill py-2 pl-5 pr-2 shadow-float',
            dirty ? 'bottom-24' : 'bottom-5',
          )}
        >
          <span role="status" className="text-body font-medium text-text">
            {selectedEntries.length} selected
          </span>
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
            {selectedEntries.some((entry) => entry.hidden) ? (
              <Button
                variant="secondary"
                disabled={hide.isPending}
                onClick={() =>
                  hide.mutate({
                    ids: selectedEntries.filter((entry) => entry.hidden).map((entry) => entry.id),
                    hidden: false,
                  })
                }
              >
                Unhide
              </Button>
            ) : null}
            {selectedEntries.some((entry) => !entry.hidden) ? (
              <Button
                variant="secondary"
                disabled={hide.isPending}
                onClick={() =>
                  hide.mutate({
                    ids: selectedEntries.filter((entry) => !entry.hidden).map((entry) => entry.id),
                    hidden: true,
                  })
                }
              >
                Hide
              </Button>
            ) : null}
            <Button variant="danger" onClick={() => setBulkDeleteOpen(true)}>
              Delete
            </Button>
          </div>
        </div>
      ) : null}

      {dirty ? (
        <StagedBar
          onDiscard={() => {
            setStaged(null)
            setAnnouncement('')
          }}
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
        open={bulkDeleteOpen}
        onOpenChange={setBulkDeleteOpen}
        title={`Delete ${selectedEntries.length} models`}
        description={`Remove ${selectedEntries.length} selected model${selectedEntries.length === 1 ? '' : 's'} from the chain? Notes already generated with them are kept. "Add all" can add them back; Hide them instead to keep them out.`}
        confirmLabel={`Delete ${selectedEntries.length} models`}
        isLoading={bulkDelete.isPending}
        onConfirm={() => bulkDelete.mutateAsync(selectedEntries.map((entry) => entry.id))}
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
