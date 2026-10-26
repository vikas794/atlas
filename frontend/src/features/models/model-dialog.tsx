import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useEffect, useId, useState } from 'react'

import { Field, FormDialog } from '../../components/shared/form-dialog'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import {
  createLlmModel,
  getAvailableModels,
  getLlmProviders,
  testLlmModel,
  updateLlmModel,
} from '../../lib/api'
import { ApiError, errorMessage, failureMessage } from '../../lib/errors'
import { queryKeys } from '../../lib/query-keys'
import type { FallbackEntry, LlmModelPatch, NewLlmModel } from '../../lib/types'

interface ModelDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Present when editing; absent when adding. */
  model?: FallbackEntry | null
}

interface Form {
  provider: string
  model_id: string
  display_name: string
  api_base: string
  input_token_limit: string
  request_timeout_seconds: string
  rpm_limit: string
  rpd_limit: string
  tpm_limit: string
  tpd_limit: string
}

const EMPTY: Form = {
  provider: '',
  model_id: '',
  display_name: '',
  api_base: '',
  input_token_limit: '',
  request_timeout_seconds: '',
  rpm_limit: '',
  rpd_limit: '',
  tpm_limit: '',
  tpd_limit: '',
}

const NUMBER_FIELDS = [
  ['input_token_limit', 'Input token limit', 'Largest transcript (in tokens) sent to this model.'],
  ['request_timeout_seconds', 'Request timeout (seconds)', 'Leave empty for the default.'],
  ['rpm_limit', 'Requests per minute', 'Leave empty for no limit.'],
  ['rpd_limit', 'Requests per day', 'Leave empty for no limit.'],
  ['tpm_limit', 'Tokens per minute', 'Leave empty for no limit.'],
  ['tpd_limit', 'Tokens per day', 'Leave empty for no limit.'],
] as const

function formFrom(model: FallbackEntry | null | undefined): Form {
  if (!model) return EMPTY
  const text = (value: number | null) => (value === null ? '' : String(value))
  return {
    provider: model.provider,
    model_id: model.model_id,
    display_name: model.display_name,
    api_base: model.api_base ?? '',
    input_token_limit: text(model.input_token_limit),
    request_timeout_seconds: text(model.request_timeout_seconds),
    rpm_limit: text(model.rpm_limit),
    rpd_limit: text(model.rpd_limit),
    tpm_limit: text(model.tpm_limit),
    tpd_limit: text(model.tpd_limit),
  }
}

/** '' -> null (no limit); otherwise a positive number, or an error sentence. */
function parseLimit(raw: string, whole: boolean): number | null | { error: string } {
  const text = raw.trim()
  if (!text) return null
  const value = Number(text)
  if (!Number.isFinite(value) || value <= 0) return { error: 'Enter a number above 0.' }
  if (whole && !Number.isInteger(value)) return { error: 'Enter a whole number.' }
  return value
}

/** Per-field messages from a 422 `details.fields` (when the backend sends them). */
function serverFieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || typeof error.details !== 'object' || !error.details) return {}
  const fields = (error.details as { fields?: unknown }).fields
  if (typeof fields !== 'object' || !fields) return {}
  return Object.fromEntries(
    Object.entries(fields as Record<string, unknown>).filter(
      (entry): entry is [string, string] => typeof entry[1] === 'string',
    ),
  )
}

/** Add a model, or edit one (design.md 6.4). Edit mode also offers a Test ping. */
export function ModelDialog({ open, onOpenChange, model }: ModelDialogProps) {
  const editing = Boolean(model)
  const [form, setForm] = useState<Form>(() => formFrom(model))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const ids = useId()
  const fid = (name: string) => `${ids}-${name}`

  // A fresh form each time the dialog opens.
  useEffect(() => {
    if (open) {
      setForm(formFrom(model))
      setErrors({})
    }
  }, [open, model])

  const providers = useQuery({
    queryKey: queryKeys.llmProviders,
    queryFn: getLlmProviders,
    enabled: open,
  })
  const providerList = providers.data?.providers ?? []
  const provider = form.provider || providerList[0]?.name || ''
  const providerInfo = providerList.find((item) => item.name === provider)
  const canFetch = Boolean(provider) && (providerInfo?.usable_key_count ?? 0) > 0

  const available = useQuery({
    queryKey: queryKeys.llmAvailableModels(provider),
    queryFn: () => getAvailableModels(provider),
    enabled: open && !editing && canFetch,
    retry: false,
    staleTime: 5 * 60_000,
  })

  const set = (name: keyof Form, value: string) => {
    setForm((current) => ({ ...current, [name]: value }))
    setErrors((current) => {
      const { [name]: _dropped, ...rest } = current
      return rest
    })
  }

  const save = useMutation({
    mutationFn: async () => {
      const limits: Record<string, number | null> = {}
      const problems: Record<string, string> = {}
      for (const [name] of NUMBER_FIELDS) {
        const parsed = parseLimit(form[name], name !== 'request_timeout_seconds')
        if (parsed !== null && typeof parsed === 'object') problems[name] = parsed.error
        else limits[name] = parsed
      }
      if (!editing && !form.model_id.trim()) problems.model_id = 'Enter a model id.'
      if (Object.keys(problems).length > 0) {
        setErrors(problems)
        throw new Error('Fix the highlighted fields.')
      }
      const apiBase = form.api_base.trim() || null
      if (model) {
        const patch: LlmModelPatch = {
          display_name: form.display_name.trim() || model.model_id,
          api_base: apiBase,
          ...limits,
        }
        if (limits.input_token_limit === null) delete patch.input_token_limit
        return updateLlmModel(model.id, patch)
      }
      const body: NewLlmModel = {
        provider,
        model_id: form.model_id.trim(),
        api_base: apiBase,
        ...limits,
        ...(form.display_name.trim() ? { display_name: form.display_name.trim() } : {}),
      }
      if (limits.input_token_limit === null) delete body.input_token_limit
      return createLlmModel(body)
    },
    onSuccess: () => {
      toast(editing ? 'Model updated.' : 'Model added to the end of the chain.')
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmFallback })
      void queryClient.invalidateQueries({ queryKey: queryKeys.llmModels })
      onOpenChange(false)
    },
    onError: (error) => {
      const fields = serverFieldErrors(error)
      if (Object.keys(fields).length > 0) setErrors((current) => ({ ...current, ...fields }))
    },
  })

  const test = useMutation({
    mutationFn: () => testLlmModel(model!.id),
    onSuccess: (result) =>
      result.success
        ? toast(`Test passed in ${result.latency_ms} ms.`)
        : toast(`Test failed: ${result.error ?? 'unknown error'}`, 'error'),
    onError: (error) => toast(failureMessage('Could not test the model', error), 'error'),
  })

  const modelIds = available.data?.models ?? []
  const generalError =
    save.isError && Object.keys(errors).length === 0 ? errorMessage(save.error) : null

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? 'Edit model' : 'Add model'}
      description={
        editing
          ? `${model?.provider} / ${model?.model_id}`
          : 'The model is added to the end of the fallback chain. Drag it up to try it sooner.'
      }
      submitLabel={editing ? 'Save changes' : 'Add model'}
      onSubmit={() => save.mutate()}
      isSubmitting={save.isPending}
      error={generalError}
      footerStart={
        editing ? (
          <Button variant="secondary" onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? 'Testing…' : 'Test'}
          </Button>
        ) : null
      }
    >
      {!editing ? (
        <>
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

          <Field
            id={fid('model_id')}
            label="Model id"
            error={errors.model_id}
            hint={
              canFetch
                ? available.isError
                  ? `Could not load the provider's models: ${errorMessage(available.error)}`
                  : `${modelIds.length} models available from this provider.`
                : 'Add a key for this provider to list its models.'
            }
          >
            <div className="flex gap-2">
              <input
                id={fid('model_id')}
                className="input"
                type="text"
                list={fid('models')}
                value={form.model_id}
                placeholder="Search or type a model id"
                aria-invalid={errors.model_id ? true : undefined}
                onChange={(event) => set('model_id', event.target.value)}
              />
              <datalist id={fid('models')}>
                {modelIds.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
              <Button
                variant="secondary"
                onClick={() => void available.refetch()}
                disabled={!canFetch || available.isFetching}
              >
                <RefreshCw aria-hidden="true" className="size-icon" />
                {available.isFetching ? 'Loading…' : 'Fetch models'}
              </Button>
            </div>
          </Field>
        </>
      ) : null}

      <Field id={fid('display_name')} label="Display name" hint="Defaults to the model id.">
        <input
          id={fid('display_name')}
          className="input"
          type="text"
          value={form.display_name}
          onChange={(event) => set('display_name', event.target.value)}
        />
      </Field>

      <Field
        id={fid('api_base')}
        label="API base URL"
        hint={
          providerInfo?.default_api_base
            ? `Leave empty to use ${providerInfo.default_api_base}.`
            : 'Leave empty to use the provider default.'
        }
      >
        <input
          id={fid('api_base')}
          className="input"
          type="text"
          value={form.api_base}
          onChange={(event) => set('api_base', event.target.value)}
        />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        {NUMBER_FIELDS.map(([name, label, hint]) => (
          <Field key={name} id={fid(name)} label={label} hint={hint} error={errors[name]}>
            <input
              id={fid(name)}
              className="input"
              type="text"
              inputMode="numeric"
              value={form[name]}
              aria-invalid={errors[name] ? true : undefined}
              onChange={(event) => set(name, event.target.value)}
            />
          </Field>
        ))}
      </div>
    </FormDialog>
  )
}
