import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import { useRef } from 'react'

import { cn } from '../../lib/utils'

export interface MenuAction {
  key: string
  label: string
  icon?: LucideIcon
  onSelect: () => void
  /** Rendered in the error colour (destructive actions). */
  danger?: boolean
  disabled?: boolean
  /** Why the item is disabled, shown as a hint under the label. */
  disabledReason?: string
}

interface ActionMenuProps {
  /** Accessible name of the trigger, e.g. "More actions" or 'Actions for "Java"'. */
  label: string
  actions: MenuAction[]
  className?: string
}

/**
 * Overflow menu (design.md 6.1, 6.2) on Radix DropdownMenu: keyboard
 * navigable, focus-trapped and closed by Escape out of the box.
 */
export function ActionMenu({ label, actions, className }: ActionMenuProps) {
  // Most actions open a confirm dialog. If the menu handed focus back to its trigger while the
  // dialog's focus trap takes over, the two fight and recurse endlessly, so after a selection the
  // dialog (or the next screen) owns focus.
  const selectedRef = useRef(false)

  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          onClick={(event) => event.stopPropagation()}
          className={cn(
            'inline-flex size-9 items-center justify-center rounded-pill border border-transparent',
            'text-text-muted transition hover:bg-surface-muted hover:text-text',
            className,
          )}
        >
          <MoreHorizontal aria-hidden="true" className="size-icon" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={4}
          onCloseAutoFocus={(event) => {
            if (selectedRef.current) {
              event.preventDefault()
              selectedRef.current = false
            }
          }}
          // React events bubble through portals; keep clicks and keys from reaching a clickable row.
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
          className="glass z-50 min-w-52 rounded-lg p-1 shadow-popover"
        >
          {actions.map((action) => {
            const Icon = action.icon
            return (
              <DropdownMenu.Item
                key={action.key}
                disabled={action.disabled}
                onSelect={() => {
                  selectedRef.current = true
                  action.onSelect()
                }}
                title={action.disabled ? action.disabledReason : undefined}
                className={cn(
                  'flex min-h-control cursor-pointer select-none items-center gap-2 rounded-lg px-3 text-body outline-none',
                  'data-[highlighted]:bg-surface-muted data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50',
                  action.danger ? 'text-st-error-fg' : 'text-text',
                )}
              >
                {Icon ? <Icon aria-hidden="true" className="size-icon shrink-0" /> : null}
                <span className="flex flex-col">
                  {action.label}
                  {action.disabled && action.disabledReason ? (
                    <span className="text-label text-text-muted">{action.disabledReason}</span>
                  ) : null}
                </span>
              </DropdownMenu.Item>
            )
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
