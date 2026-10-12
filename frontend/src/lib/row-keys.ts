import type { KeyboardEvent } from 'react'

/**
 * Keyboard support for clickable table rows (design.md 8): Enter opens the
 * row, ArrowUp / ArrowDown move focus to the previous / next row. Keys pressed
 * on a control inside the row (a button, a link, a menu) are left alone.
 */
export function onRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, open: () => void): void {
  if (event.target !== event.currentTarget) return
  if (event.key === 'Enter') {
    event.preventDefault()
    open()
    return
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
  const sibling =
    event.key === 'ArrowDown'
      ? event.currentTarget.nextElementSibling
      : event.currentTarget.previousElementSibling
  if (sibling instanceof HTMLElement && sibling.tabIndex >= 0) {
    event.preventDefault()
    sibling.focus()
  }
}
