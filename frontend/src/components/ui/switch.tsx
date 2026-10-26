import { cn } from '../../lib/utils'

interface SwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Accessible name, e.g. 'Enable Gemini 3 Flash'. */
  label: string
  disabled?: boolean
  className?: string
}

/** On/off toggle (design.md 6.4, 6.5): a real button with `role="switch"`, keyboard operable. */
export function Switch({ checked, onCheckedChange, label, disabled, className }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation()
        onCheckedChange(!checked)
      }}
      className={cn(
        'relative inline-flex h-5 w-8 shrink-0 items-center rounded-pill border transition',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'border-transparent bg-accent' : 'border-border bg-surface-muted',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block size-3 rounded-pill bg-surface shadow-popover transition-transform',
          checked ? 'translate-x-4' : 'translate-x-1',
        )}
      />
    </button>
  )
}
