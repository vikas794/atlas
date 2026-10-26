import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Lock, RotateCcw, SlidersHorizontal } from 'lucide-react'
import { useId, useState } from 'react'
import { Link } from 'react-router-dom'

import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { DisabledReason } from '../../components/shared/disabled-reason'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import { buttonClasses } from '../../components/ui/button-styles'
import { Card } from '../../components/ui/card'
import { getSettings, resetSettings, updateSettings } from '../../lib/api'
import { ApiError, errorMessage, failureMessage } from '../../lib/errors'
import { queryKeys } from '../../lib/query-keys'
import type { SettingField, SettingValue, SettingsResponse } from '../../lib/types'
import { cn } from '../../lib/utils'

/** Edited values by setting name; numbers stay text while typing. */
type Draft = Record<string, string | boolean>

const RUN_MODE_KEY = 'max_concurrent_videos'

function asText(value: SettingValue): string {
  return String(value)
}

/** Draft entry -> API value, or an error sentence when it is not a valid number. */
function toValue(field: SettingField, raw: string | boolean): SettingValue | { error: string } {
  if (field.kind === 'bool') return Boolean(raw)
  const text = String(raw).trim()
  if (field.kind === 'int' || field.kind === 'float') {
    const parsed = Number(text)
    if (!text || !Number.isFinite(parsed)) return { error: 'Enter a number.' }
    if (field.kind === 'int' && !Number.isInteger(parsed)) return { error: 'Enter a whole number.' }
    return parsed
  }
  return text
}

function sameValue(field: SettingField, raw: string | boolean): boolean {
  const value = toValue(field, raw)
  return typeof value !== 'object' && value === field.value
}

/** Per-field messages from a 422 `details.fields`. */
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

/**
 * Application configuration, editable in the UI and saved in the database. Environment
 * variables still win (those fields are locked); most values apply to the running app on save.
 */
export function AppConfigPanel() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [draft, setDraft] = useState<Draft>({})
  const [panelOpen, setPanelOpen] = useState(true)
  const panelId = useId()
  const [open, setOpen] = useState<Record<string, boolean>>({ pipeline: true })

  // Closing the whole panel also closes every group inside it.
  const togglePanel = () => {
    if (panelOpen) setOpen({})
    setPanelOpen(!panelOpen)
  }
  const [clientErrors, setClientErrors] = useState<Record<string, string>>({})
  const [confirmReset, setConfirmReset] = useState(false)

  const query = useQuery({ queryKey: queryKeys.settings, queryFn: getSettings })

  const applyResponse = (data: SettingsResponse) => {
    queryClient.setQueryData(queryKeys.settings, data)
    setDraft({})
    setClientErrors({})
  }

  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (data) => {
      applyResponse(data)
      toast(
        data.restart_required
          ? 'Settings saved. Some changes need a restart.'
          : 'Settings saved and applied.',
      )
    },
    onError: (error) => toast(failureMessage('Could not save the settings', error), 'error'),
  })

  const reset = useMutation({
    mutationFn: resetSettings,
    onSuccess: (data) => {
      applyResponse(data)
      toast('Settings reset to their defaults.')
    },
    onError: (error) => toast(failureMessage('Could not reset the settings', error), 'error'),
  })

  const data = query.data
  const fieldsByKey = new Map((data?.fields ?? []).map((field) => [field.key, field]))
  const dirtyKeys = Object.keys(draft).filter((key) => {
    const field = fieldsByKey.get(key)
    return field !== undefined && !sameValue(field, draft[key])
  })
  const serverErrors = save.isError ? serverFieldErrors(save.error) : {}
  const hasSaved = (data?.fields ?? []).some((field) => field.source === 'db')

  const change = (key: string, raw: string | boolean) => {
    setDraft((current) => ({ ...current, [key]: raw }))
    setClientErrors((current) => {
      const { [key]: _dropped, ...rest } = current
      return rest
    })
    if (save.isError) save.reset()
  }

  const handleSave = () => {
    const values: Record<string, SettingValue> = {}
    const errors: Record<string, string> = {}
    for (const key of dirtyKeys) {
      const field = fieldsByKey.get(key)
      if (!field) continue
      const value = toValue(field, draft[key])
      if (typeof value === 'object') errors[key] = value.error
      else values[key] = value
    }
    setClientErrors(errors)
    if (Object.keys(errors).length === 0) save.mutate(values)
  }

  const valueOf = (field: SettingField): string | boolean =>
    field.key in draft
      ? draft[field.key]
      : field.kind === 'bool'
        ? Boolean(field.value)
        : asText(field.value)

  const errorOf = (key: string) => clientErrors[key] ?? serverErrors[key]

  return (
    <section id="app-config" className="space-y-4">
      <Card className="p-5">
        <button
          type="button"
          aria-expanded={panelOpen}
          aria-controls={panelId}
          onClick={togglePanel}
          className="flex w-full flex-wrap items-start justify-between gap-4 rounded text-left transition hover:bg-surface-muted"
        >
          <div>
            <h2 className="flex items-center gap-2 text-section text-text">
              <SlidersHorizontal className="size-icon-lg text-accent" aria-hidden="true" />
              Application configuration
            </h2>
            <p className="mt-1 text-body text-text-muted">
              Concurrency, timeouts, retries and more. Changes are saved in the database and apply
              to the running app unless marked as needing a restart.
            </p>
          </div>
          <ChevronDown
            aria-hidden="true"
            className={cn(
              'size-icon shrink-0 text-text-muted transition',
              panelOpen && 'rotate-180',
            )}
          />
        </button>

        <div id={panelId} hidden={!panelOpen}>
          {query.isPending ? (
            <p className="mt-4 text-body text-text-muted">Loading configuration…</p>
          ) : null}
          {query.isError ? (
            <p role="alert" className="mt-4 text-body text-st-error-fg">
              Could not load the configuration: {errorMessage(query.error)}
            </p>
          ) : null}

          {data ? (
            <div className="mt-5 space-y-3">
              {data.restart_required ? (
                <p
                  role="status"
                  className="rounded border border-border bg-st-warning-bg px-3 py-2 text-body text-st-warning-fg"
                >
                  Restart required: some saved settings only take effect after Atlas restarts.
                </p>
              ) : null}

              {data.groups.map((group) => {
                const fields = data.fields.filter((field) => field.group === group.id)
                if (fields.length === 0) return null
                return (
                  <Group
                    key={group.id}
                    id={group.id}
                    label={group.label}
                    open={open[group.id] === true}
                    onToggle={() =>
                      setOpen((current) => ({ ...current, [group.id]: !current[group.id] }))
                    }
                  >
                    {group.id === 'pipeline' ? (
                      <RunMode
                        field={fieldsByKey.get(RUN_MODE_KEY)}
                        value={String(
                          RUN_MODE_KEY in draft
                            ? draft[RUN_MODE_KEY]
                            : (fieldsByKey.get(RUN_MODE_KEY)?.value ?? 1),
                        )}
                        onChange={(next) => change(RUN_MODE_KEY, next)}
                        error={errorOf(RUN_MODE_KEY)}
                      />
                    ) : null}
                    {group.id === 'notes' ? <ModelsLinkCard /> : null}
                    {fields
                      .filter((field) => field.key !== RUN_MODE_KEY)
                      .map((field) => (
                        <FieldRow
                          key={field.key}
                          field={field}
                          value={valueOf(field)}
                          error={errorOf(field.key)}
                          onChange={(raw) => change(field.key, raw)}
                        />
                      ))}
                  </Group>
                )
              })}

              <div className="flex flex-wrap items-center gap-3 pt-1">
                <DisabledReason reason={dirtyKeys.length === 0 ? 'No changes to save.' : null}>
                  <Button onClick={handleSave} disabled={dirtyKeys.length === 0 || save.isPending}>
                    {save.isPending ? 'Saving…' : 'Save changes'}
                  </Button>
                </DisabledReason>
                <DisabledReason
                  reason={!hasSaved ? 'Nothing has been changed from the defaults.' : null}
                >
                  <Button
                    variant="secondary"
                    onClick={() => setConfirmReset(true)}
                    disabled={!hasSaved || reset.isPending}
                  >
                    <RotateCcw className="size-icon" aria-hidden="true" />
                    Reset to defaults
                  </Button>
                </DisabledReason>
                {dirtyKeys.length > 0 ? (
                  <span className="text-label text-text-muted" role="status">
                    {dirtyKeys.length} unsaved {dirtyKeys.length === 1 ? 'change' : 'changes'}
                  </span>
                ) : null}
              </div>
              {save.isError && Object.keys(serverErrors).length === 0 ? (
                <p role="alert" className="text-label text-st-error-fg">
                  {errorMessage(save.error)}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </Card>

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Reset to defaults"
        description="Every saved change is removed and the values return to config.yaml and the built-in defaults. Environment variables are not affected."
        confirmLabel="Reset to defaults"
        isLoading={reset.isPending}
        onConfirm={() => reset.mutateAsync()}
      />
    </section>
  )
}

function Group({
  id,
  label,
  open,
  onToggle,
  children,
}: {
  id: string
  label: string
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  const panelId = useId()
  return (
    <div className="rounded border border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        data-group={id}
        className="flex w-full items-center justify-between gap-3 rounded px-4 py-3 text-left transition hover:bg-surface-muted"
      >
        <h3 className="text-body font-medium text-text">{label}</h3>
        <ChevronDown
          aria-hidden="true"
          className={cn('size-icon shrink-0 text-text-muted transition', open && 'rotate-180')}
        />
      </button>
      <div id={panelId} hidden={!open} className="space-y-4 border-t border-border p-4">
        {children}
      </div>
    </div>
  )
}

/** Where the AI provider, models and API keys are managed now. */
function ModelsLinkCard() {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-border bg-surface-muted px-4 py-3">
      <p className="text-body text-text-muted">
        Which models write the notes, in what order, and their API keys are managed on the Models
        and Keys screens.
      </p>
      <div className="flex gap-3">
        <Link to="/models" className={buttonClasses({ variant: 'secondary' })}>
          Models
        </Link>
        <Link to="/keys" className={buttonClasses({ variant: 'secondary' })}>
          Keys
        </Link>
      </div>
    </div>
  )
}

function RunMode({
  field,
  value,
  onChange,
  error,
}: {
  field: SettingField | undefined
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  const groupId = useId()
  if (!field) return null
  const locked = field.locked || !field.editable
  const parallel = Number(value) !== 1
  const options = [
    { id: 'serial', label: 'Serial', hint: 'One video at a time', active: !parallel },
    { id: 'parallel', label: 'Parallel', hint: 'Several videos at once', active: parallel },
  ]

  return (
    <fieldset className="space-y-2" disabled={locked}>
      <legend className="text-body font-medium text-text">Run mode</legend>
      <div role="radiogroup" aria-labelledby={groupId} className="flex flex-wrap gap-2">
        <span id={groupId} className="sr-only">
          Run mode
        </span>
        {options.map((option) => (
          <label
            key={option.id}
            className={cn(
              'flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-body',
              option.active
                ? 'border-accent bg-surface-muted text-text'
                : 'border-border text-text-muted',
              locked && 'cursor-not-allowed opacity-50',
            )}
          >
            <input
              type="radio"
              name="run-mode"
              checked={option.active}
              onChange={() =>
                onChange(option.id === 'serial' ? '1' : String(Math.max(2, Number(value) || 2)))
              }
            />
            <span className="font-medium">{option.label}</span>
            <span className="text-label text-text-muted">{option.hint}</span>
          </label>
        ))}
      </div>
      {parallel ? (
        <FieldRow
          field={{
            ...field,
            label: 'Videos at once',
            help: 'How many videos are processed in parallel (2 or more).',
            minimum: 2,
          }}
          value={value}
          onChange={(raw) => onChange(String(raw))}
          error={error}
          hideBadges
        />
      ) : error ? (
        <p role="alert" className="text-label text-st-error-fg">
          {error}
        </p>
      ) : null}
      <p className="text-label text-text-muted">{field.help}</p>
      <Badges field={field} />
    </fieldset>
  )
}

function Badges({ field }: { field: SettingField }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-label">
      {field.apply === 'restart' ? (
        <span className="rounded border border-border bg-st-neutral-bg px-2 py-0.5 text-st-neutral-fg">
          Needs restart
        </span>
      ) : null}
      {field.locked ? (
        <span className="flex items-center gap-1 rounded border border-border bg-st-neutral-bg px-2 py-0.5 text-st-neutral-fg">
          <Lock className="size-icon" aria-hidden="true" />
          Set by an environment variable
        </span>
      ) : null}
      {!field.editable ? (
        <span className="rounded border border-border bg-st-neutral-bg px-2 py-0.5 text-st-neutral-fg">
          Set in config.yaml or the environment
        </span>
      ) : null}
      {field.source === 'db' ? (
        <span className="rounded border border-border bg-st-success-bg px-2 py-0.5 text-st-success-fg">
          Changed from {String(field.default)}
        </span>
      ) : null}
    </div>
  )
}

function FieldRow({
  field,
  value,
  error,
  onChange,
  hideBadges = false,
}: {
  field: SettingField
  value: string | boolean
  error?: string
  onChange: (raw: string | boolean) => void
  hideBadges?: boolean
}) {
  const id = useId()
  const describedBy = `${id}-help`
  const disabled = field.locked || !field.editable

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-body font-medium text-text">
        {field.label}
        {field.unit ? (
          <span className="ml-1 text-label text-text-muted">({field.unit})</span>
        ) : null}
      </label>

      {field.kind === 'bool' ? (
        <div>
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value)}
            disabled={disabled}
            aria-describedby={describedBy}
            onChange={(event) => onChange(event.target.checked)}
          />
        </div>
      ) : field.choices.length > 0 ? (
        <select
          id={id}
          className="input max-w-xs"
          value={String(value)}
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
        >
          {field.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          className="input max-w-xs"
          type={field.kind === 'int' || field.kind === 'float' ? 'number' : 'text'}
          inputMode={
            field.kind === 'int' ? 'numeric' : field.kind === 'float' ? 'decimal' : undefined
          }
          min={field.minimum ?? undefined}
          step={field.kind === 'float' ? 'any' : 1}
          value={String(value)}
          disabled={disabled}
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      )}

      {error ? (
        <p role="alert" className="text-label text-st-error-fg">
          {error}
        </p>
      ) : null}
      <p id={describedBy} className="text-label text-text-muted">
        {field.help}
      </p>
      {hideBadges ? null : <Badges field={field} />}
    </div>
  )
}
