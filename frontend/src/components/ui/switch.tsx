import { Check } from 'lucide-react'

import { cn } from '../../lib/utils'

interface SwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  /** Accessible name, e.g. 'Enable Gemini 3 Flash'. */
  label: string
  disabled?: boolean
  className?: string
}

/**
 * On/off control drawn as a circle: filled accent with a check when on, an empty ring when off.
 * A real button with `role="switch"`, keyboard operable (design.md 6.4, 6.5).
 */
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
        'inline-flex size-7 shrink-0 items-center justify-center rounded-pill border-2 transition',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked
          ? 'border-transparent bg-accent text-on-accent shadow-card hover:brightness-110'
          : 'border-border bg-transparent text-transparent hover:border-text-muted',
        className,
      )}
    >
      <Check aria-hidden="true" strokeWidth={3} className="size-icon" />
    </button>
  )
}
