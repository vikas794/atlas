import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { EmptyState, ErrorState, SkeletonBar } from '../../components/shared/state-panels'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import { buttonClasses } from '../../components/ui/button-styles'
import {
  checkAllLlmKeys,
  checkLlmKey,
  clearKeyCooldowns,
  deleteLlmKey,
  getLlmKeys,
  getLlmProviders,
  updateLlmKey,
} from '../../lib/api'
import { errorMessage, failureMessage } from '../../lib/errors'
import { pluralize } from '../../lib/format'
import { groupKeys } from '../../lib/key-groups'
import { queryKeys } from '../../lib/query-keys'
import type { LlmKey } from '../../lib/types'
import { KeyFormDialog, RevealKeyDialog } from './key-dialogs'
import { KeyList } from './key-list'

/** Polled so cooldowns and statuses stay current while the page is open. */
const KEYS_POLL_MS = 30_000

/**
 * Keys (design.md 6.5): API keys grouped by provider with masked values, status, cooldowns and
 * per-key and batch actions.
 */
export function KeysScreen() {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [formOpen, setFormOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<LlmKey | null>(null)
  const [revealTarget, setRevealTarget] = useState<LlmKey | null>(null)
  const [deleteTargets, setDeleteTargets] = useState<LlmKey[]>([])
  const [checkingIds, setCheckingIds] = useState<Set<number>>(new Set())

  const queryClient = useQueryClient()
  const { toast } = useToast()

  const keysQuery = useQuery({
    queryKey: queryKeys.llmKeys,
    queryFn: getLlmKeys,
    refetchInterval: KEYS_POLL_MS,
  })
  const providersQuery = useQuery({ queryKey: queryKeys.llmProviders, queryFn: getLlmProviders })

  const keys = useMemo(() => keysQuery.data?.keys ?? [], [keysQuery.data])
  const labels = useMemo(
    () => new Map((providersQuery.data?.providers ?? []).map((p) => [p.name, p.label])),
    [providersQuery.data],
  )
  const groups = useMemo(() => groupKeys(keys, labels), [keys, labels])
  // Drop selections for keys that no longer exist (deleted elsewhere or after a refetch).
  const selectedKeys = useMemo(() => keys.filter((key) => selected.has(key.id)), [keys, selected])

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.llmKeys })
    void queryClient.invalidateQueries({ queryKey: queryKeys.llmProviders })
    void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
  }

  const replaceKey = (updated: LlmKey) =>
    queryClient.setQueryData<{ keys: LlmKey[] }>(queryKeys.llmKeys, (current) =>
      current ? { keys: current.keys.map((k) => (k.id === updated.id ? updated : k)) } : current,
    )

  const setEnabled = useMutation({
    mutationFn: ({ ids, enabled }: { ids: number[]; enabled: boolean }) =>
      Promise.all(ids.map((id) => updateLlmKey(id, { enabled }))),
    onSuccess: refresh,
    onError: (error) => {
      toast(failureMessage('Could not change the keys', error), 'error')
      refresh()
    },
  })

  const check = useMutation({
    mutationFn: (key: LlmKey) => checkLlmKey(key.id),
    onMutate: (key) => setCheckingIds((current) => new Set(current).add(key.id)),
    onSuccess: (updated) => {
      replaceKey(updated)
      toast(
        updated.status === 'healthy'
          ? `${updated.label} is healthy.`
          : `${updated.label}: ${updated.last_health_error ?? updated.status}`,
        updated.status === 'healthy' ? 'success' : 'info',
      )
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
    },
    onError: (error) => toast(failureMessage('Could not check the key', error), 'error'),
    onSettled: (_data, _error, key) =>
      setCheckingIds((current) => {
        const next = new Set(current)
        next.delete(key.id)
        return next
      }),
  })

  const checkAll = useMutation({
    mutationFn: checkAllLlmKeys,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.llmKeys, data)
      const healthy = data.keys.filter((key) => key.status === 'healthy').length
      toast(`Checked ${pluralize(data.keys.length, 'key')}: ${healthy} healthy.`)
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
    },
    onError: (error) => toast(failureMessage('Could not check the keys', error), 'error'),
  })

  const clearCooldowns = useMutation({
    mutationFn: (key: LlmKey) => clearKeyCooldowns(key.id),
    onSuccess: (_data, key) => {
      toast(`Cleared the cooldowns on ${key.label}.`)
      refresh()
    },
    onError: (error) => toast(failureMessage('Could not clear the cooldowns', error), 'error'),
  })

  const remove = useMutation({
    mutationFn: (targets: LlmKey[]) => Promise.all(targets.map((key) => deleteLlmKey(key.id))),
    onSuccess: (_data, targets) => {
      toast(`Deleted ${pluralize(targets.length, 'key')}.`)
      setSelected(new Set())
      refresh()
    },
    onError: (error) => {
      toast(failureMessage('Could not delete the keys', error), 'error')
      refresh()
    },
  })

  const toggleSelected = (ids: number[], on: boolean) =>
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      return next
    })

  const openAdd = () => {
    setEditTarget(null)
    setFormOpen(true)
  }
  const openEdit = (key: LlmKey) => {
    setEditTarget(key)
    setFormOpen(true)
  }

  const batchIds = selectedKeys.map((key) => key.id)

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-page text-text">Keys</h1>
          <p className="mt-1 text-body text-text-muted">
            API keys for the providers behind your models. Keys rotate when one is rate-limited or
            rejected.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link to="/models" className={buttonClasses({ variant: 'secondary' })}>
            Models
          </Link>
          <Button
            variant="secondary"
            onClick={() => checkAll.mutate()}
            disabled={checkAll.isPending || keys.length === 0}
          >
            <RefreshCw aria-hidden="true" className="size-icon" />
            {checkAll.isPending ? 'Checking…' : 'Check all'}
          </Button>
          <Button onClick={openAdd}>
            <Plus aria-hidden="true" className="size-icon" />
            Add key
          </Button>
        </div>
      </div>

      {selectedKeys.length > 0 ? (
        <div
          role="region"
          aria-label="Selected keys"
          className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded border border-border bg-accent-soft px-3 py-2"
        >
          <span role="status" className="text-body text-text">
            {selectedKeys.length} selected
          </span>
          <div className="flex gap-3">
            <Button
              variant="secondary"
              onClick={() => setEnabled.mutate({ ids: batchIds, enabled: true })}
              disabled={setEnabled.isPending}
            >
              Enable
            </Button>
            <Button
              variant="secondary"
              onClick={() => setEnabled.mutate({ ids: batchIds, enabled: false })}
              disabled={setEnabled.isPending}
            >
              Disable
            </Button>
            <Button variant="danger" onClick={() => setDeleteTargets(selectedKeys)}>
              Delete
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-5">
        {keysQuery.isError ? (
          <ErrorState
            title="Could not load the keys"
            message={errorMessage(keysQuery.error)}
            onRetry={() => void keysQuery.refetch()}
          />
        ) : keysQuery.isPending ? (
          <div aria-hidden="true" className="space-y-3 rounded border border-border bg-surface p-5">
            <SkeletonBar className="w-48" />
            <SkeletonBar className="w-full" />
            <SkeletonBar className="w-full" />
          </div>
        ) : keys.length === 0 ? (
          <EmptyState title="No keys yet. Add one so a provider can serve your models.">
            <Button className="mt-3" onClick={openAdd}>
              <Plus aria-hidden="true" className="size-icon" />
              Add key
            </Button>
          </EmptyState>
        ) : (
          <KeyList
            groups={groups}
            selected={selected}
            checkingIds={checkingIds}
            onToggleSelected={toggleSelected}
            onToggleEnabled={(ids, enabled) => setEnabled.mutate({ ids, enabled })}
            onCheck={(key) => check.mutate(key)}
            onEdit={openEdit}
            onReveal={setRevealTarget}
            onClearCooldowns={(key) => clearCooldowns.mutate(key)}
            onDelete={(key) => setDeleteTargets([key])}
          />
        )}
      </div>

      <KeyFormDialog open={formOpen} onOpenChange={setFormOpen} keyRow={editTarget} />
      <RevealKeyDialog keyRow={revealTarget} onClose={() => setRevealTarget(null)} />

      <ConfirmDialog
        open={deleteTargets.length > 0}
        onOpenChange={(open) => {
          if (!open) setDeleteTargets([])
        }}
        title={deleteTargets.length === 1 ? 'Delete key' : 'Delete keys'}
        description={
          deleteTargets.length === 1
            ? `Delete the key "${deleteTargets[0].label}"? Models that depend only on this provider's keys stop being served until another key is added.`
            : `Delete ${pluralize(deleteTargets.length, 'key')}? Models that depend only on these keys stop being served until another key is added.`
        }
        confirmLabel={
          deleteTargets.length === 1 ? 'Delete key' : `Delete ${deleteTargets.length} keys`
        }
        isLoading={remove.isPending}
        onConfirm={() => remove.mutateAsync(deleteTargets)}
      />
    </div>
  )
}
