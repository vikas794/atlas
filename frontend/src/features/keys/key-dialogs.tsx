import * as Dialog from '@radix-ui/react-dialog'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Eye, EyeOff, X } from 'lucide-react'
import { useEffect, useId, useState } from 'react'

import { Field, FormDialog } from '../../components/shared/form-dialog'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import { createLlmKey, getLlmProviders, revealLlmKey, updateLlmKey } from '../../lib/api'
import { errorMessage } from '../../lib/errors'
import { queryKeys } from '../../lib/query-keys'
import type { LlmKey, LlmKeyPatch } from '../../lib/types'

interface KeyFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present when editing; absent when adding. */
  keyRow?: LlmKey | null
  /** Provider preselected when adding. */
  defaultProvider?: string
}

interface Form {
  provider: string
  label: string
  key: string
  monthly_request_cap: string
  monthly_token_cap: string
}

function formFrom(keyRow: LlmKey | null | undefined, defaultProvider: string): Form {
  return {
    provider: keyRow?.provider ?? defaultProvider,
    label: keyRow?.label ?? '',
    key: '',
    monthly_request_cap: keyRow?.monthly_request_cap ? String(keyRow.monthly_request_cap) : '',
    monthly_token_cap: keyRow?.monthly_token_cap ? String(keyRow.monthly_token_cap) : '',
  }
}

/** '' or 0 -> null (unlimited); otherwise a positive whole number, or an error sentence. */
function parseCap(raw: string): number | null | { error: string } {
  const text = raw.trim()
  if (!text) return null
  const value = Number(text)
  if (!Number.isInteger(value) || value < 0)
    return { error: 'Enter a whole number, or 0 for unlimited.' }
  return value === 0 ? null : value
}

/** Add or edit an API key (design.md 6.5). Edit sends only changed fields; empty Key keeps the key. */
export function KeyFormDialog({
  open,
  onOpenChange,
  keyRow,
  defaultProvider = '',
}: KeyFormDialogProps) {
  const editing = Boolean(keyRow)
  const [form, setForm] = useState<Form>(() => formFrom(keyRow, defaultProvider))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [showKey, setShowKey] = useState(false)
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const ids = useId()
  const fid = (name: string) => `${ids}-${name}`

  const providers = useQuery({
    queryKey: queryKeys.llmProviders,
    queryFn: getLlmProviders,
    enabled: open,
  })
  const providerList = providers.data?.providers ?? []

  useEffect(() => {
    if (open) {
      setForm(formFrom(keyRow, defaultProvider))
      setErrors({})
      setShowKey(false)
    }
  }, [open, keyRow, defaultProvider])

  const provider = form.provider || providerList[0]?.name || ''

  const set = (name: keyof Form, value: string) => {
    setForm((current) => ({ ...current, [name]: value }))
    setErrors((current) => {
      const { [name]: _dropped, ...rest } = current
      return rest
    })
  }

  const save = useMutation({
    mutationFn: async () => {
      const problems: Record<string, string> = {}
      const requestCap = parseCap(form.monthly_request_cap)
      const tokenCap = parseCap(form.monthly_token_cap)
      if (requestCap !== null && typeof requestCap === 'object')
        problems.monthly_request_cap = requestCap.error
      if (tokenCap !== null && typeof tokenCap === 'object')
        problems.monthly_token_cap = tokenCap.error
      if (!editing && !form.key.trim()) problems.key = 'Enter the API key.'
      if (Object.keys(problems).length > 0) {
        setErrors(problems)
        throw new Error('Fix the highlighted fields.')
      }
      const requestValue = requestCap as number | null
      const tokenValue = tokenCap as number | null

      if (keyRow) {
        const patch: LlmKeyPatch = {}
        if (form.label.trim() !== keyRow.label) patch.label = form.label.trim() || keyRow.label
        if (form.key.trim()) patch.key = form.key.trim()
        if (requestValue !== (keyRow.monthly_request_cap || null))
          patch.monthly_request_cap = requestValue
        if (tokenValue !== (keyRow.monthly_token_cap || null)) patch.monthly_token_cap = tokenValue
        return updateLlmKey(keyRow.id, patch)
      }
      return createLlmKey({
        provider,
        key: form.key.trim(),
        ...(form.label.trim() ? { label: form.label.trim() } : {}),
        monthly_request_cap: requestValue,
        monthly_token_cap: tokenValue,
      })
    },
    onSuccess: () => {
      toast(editing ? 'Key updated.' : 'Key added.')
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmKeys })
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmProviders })
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
      onOpenChange(false)
    },
  })

  const generalError =
    save.isError && Object.keys(errors).length === 0 ? errorMessage(save.error) : null

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? 'Edit key' : 'Add key'}
      description={
        editing
          ? `${keyRow?.provider} key ${keyRow?.masked_key}`
          : 'The key is stored in the Atlas database and shown masked everywhere else.'
      }
      submitLabel={editing ? 'Save changes' : 'Add key'}
      onSubmit={() => save.mutate()}
      isSubmitting={save.isPending}
      error={generalError}
    >
      {!editing ? (
        <Field id={fid('provider')} label="Provider">
          <select
            id={fid('provider')}
            className="input"
            value={provider}
            onChange={(event) => set('provider', event.target.value)}
          >
            {providerList.map((item) => (
              <option key={item.name} value={item.name}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
      ) : null}

      <Field id={fid('label')} label="Label" hint="Optional. A name to tell keys apart.">
        <input
          id={fid('label')}
          className="input"
          type="text"
          value={form.label}
          onChange={(event) => set('label', event.target.value)}
        />
      </Field>

      <Field
        id={fid('key')}
        label="Key"
        error={errors.key}
        hint={editing ? 'Leave empty to keep the stored key.' : undefined}
      >
        <div className="flex gap-2">
          <input
            id={fid('key')}
            className="input"
            type={showKey ? 'text' : 'password'}
            autoComplete="off"
            spellCheck={false}
            value={form.key}
            aria-invalid={errors.key ? true : undefined}
            onChange={(event) => set('key', event.target.value)}
          />
          <Button
            variant="secondary"
            aria-label={showKey ? 'Hide key' : 'Show key'}
            aria-pressed={showKey}
            onClick={() => setShowKey((current) => !current)}
          >
            {showKey ? (
              <EyeOff aria-hidden="true" className="size-icon" />
            ) : (
              <Eye aria-hidden="true" className="size-icon" />
            )}
          </Button>
        </div>
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          id={fid('request_cap')}
          label="Monthly request cap"
          hint="0 or empty means unlimited."
          error={errors.monthly_request_cap}
        >
          <input
            id={fid('request_cap')}
            className="input"
            type="text"
            inputMode="numeric"
            value={form.monthly_request_cap}
            aria-invalid={errors.monthly_request_cap ? true : undefined}
            onChange={(event) => set('monthly_request_cap', event.target.value)}
          />
        </Field>
        <Field
          id={fid('token_cap')}
          label="Monthly token cap"
          hint="0 or empty means unlimited."
          error={errors.monthly_token_cap}
        >
          <input
            id={fid('token_cap')}
            className="input"
            type="text"
            inputMode="numeric"
            value={form.monthly_token_cap}
            aria-invalid={errors.monthly_token_cap ? true : undefined}
            onChange={(event) => set('monthly_token_cap', event.target.value)}
          />
        </Field>
      </div>
    </FormDialog>
  )
}

/**
 * Shows the full key once the user asks for it (design.md 6.5). The value lives only in the body's
 * query state, which unmounts with the dialog, so it never reaches a list, toast or log.
 */
export function RevealKeyDialog({
  keyRow,
  onClose,
}: {
  keyRow: LlmKey | null
  onClose: () => void
}) {
  return (
    <Dialog.Root open={keyRow !== null} onOpenChange={(open) => (open ? null : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-text/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-modal -translate-x-1/2 -translate-y-1/2 rounded-modal border border-border bg-surface p-5 shadow-modal">
          {keyRow ? <RevealBody key={keyRow.id} keyRow={keyRow} onClose={onClose} /> : null}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function RevealBody({ keyRow, onClose }: { keyRow: LlmKey; onClose: () => void }) {
  const { toast } = useToast()
  const reveal = useMutation({ mutationFn: (id: number) => revealLlmKey(id) })
  const { mutate } = reveal
  const id = keyRow.id

  useEffect(() => {
    mutate(id)
  }, [id, mutate])

  const value = reveal.data?.key ?? null

  const copy = async () => {
    if (value === null) return
    try {
      await navigator.clipboard.writeText(value)
      toast('Key copied.')
    } catch {
      toast('Could not copy. Select the key and copy it manually.', 'error')
    }
  }

  return (
    <>
      <div className="flex items-start gap-4">
        <div className="flex-1">
          <Dialog.Title className="text-section text-text">Reveal key</Dialog.Title>
          <Dialog.Description className="mt-1 text-body text-text-muted">
            {keyRow.label} ({keyRow.provider})
          </Dialog.Description>
        </div>
        <Dialog.Close asChild>
          <button
            type="button"
            className="rounded text-text-muted transition hover:text-text"
            aria-label="Close dialog"
          >
            <X aria-hidden="true" className="size-icon" />
          </button>
        </Dialog.Close>
      </div>

      <div className="mt-4">
        {reveal.isPending ? <p className="text-body text-text-muted">Loading…</p> : null}
        {reveal.isError ? (
          <p role="alert" className="text-label text-st-error-fg">
            Could not reveal the key: {errorMessage(reveal.error)}
          </p>
        ) : null}
        {value !== null ? (
          <div className="flex gap-2">
            <input
              className="input font-mono"
              readOnly
              aria-label="API key"
              value={value}
              onFocus={(event) => event.currentTarget.select()}
            />
            <Button variant="secondary" onClick={() => void copy()}>
              <Copy aria-hidden="true" className="size-icon" />
              Copy
            </Button>
          </div>
        ) : null}
      </div>

      <div className="mt-5 flex justify-end">
        <Button variant="secondary" onClick={onClose}>
          Close
        </Button>
      </div>
    </>
  )
}
