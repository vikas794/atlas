import { cn } from '../../lib/utils'

/** Section 7: primary (accent fill), secondary (surface + border), ghost, danger. */
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

export type ButtonSize = 'sm' | 'md'

const variants: Record<ButtonVariant, string> = {
  primary:
    'border-transparent bg-accent text-on-accent shadow-card hover:-translate-y-px hover:shadow-card-hover hover:brightness-110 active:translate-y-0',
  secondary: 'border-border bg-surface text-text hover:border-text-muted hover:bg-surface-muted',
  ghost: 'border-transparent bg-transparent text-text-muted hover:bg-surface-muted hover:text-text',
  danger: 'border-transparent bg-st-error-fg text-on-accent hover:brightness-110',
}

const sizes: Record<ButtonSize, string> = {
  // Section 5: controls are 32px tall.
  sm: 'h-control px-4',
  md: 'h-control px-5',
}

/**
 * Class string shared by `<Button>` and by links that must look like buttons
 * (so a row never nests a `<button>` inside an `<a>`).
 */
export function buttonClasses(
  options: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {},
): string {
  const { variant = 'primary', size = 'md', className } = options
  return cn(
    'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-pill border text-body font-medium transition',
    // Section 7: disabled controls sit at 50% opacity; wrap in <DisabledReason> to say why.
    'disabled:cursor-not-allowed disabled:opacity-50',
    variants[variant],
    sizes[size],
    className,
  )
}
