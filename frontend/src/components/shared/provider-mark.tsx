import { cn } from '../../lib/utils'

/** Fixed gradient per well-known provider; others get one derived from the name. */
const GRADIENTS = [
  'from-accent to-accent-3',
  'from-accent-2 to-accent',
  'from-accent-3 to-accent-2',
  'from-st-success-fg to-accent-2',
  'from-st-warn-fg to-accent-3',
  'from-st-error-fg to-accent-3',
] as const

function pick(name: string): string {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return GRADIENTS[hash % GRADIENTS.length]
}

/** Monogram tile that gives each provider a stable identity colour. */
export function ProviderMark({
  name,
  size = 'md',
  className,
}: {
  name: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const initials =
    name
      .replace(/[^a-z0-9]/gi, '')
      .slice(0, 2)
      .toUpperCase() || '?'
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center bg-gradient-to-br font-display font-semibold text-on-accent shadow-card',
        pick(name.toLowerCase()),
        size === 'sm' && 'size-[26px] rounded-lg text-[10px]',
        size === 'md' && 'size-9 rounded-lg text-label',
        size === 'lg' && 'size-11 rounded-xl text-body',
        className,
      )}
    >
      {initials}
    </span>
  )
}
