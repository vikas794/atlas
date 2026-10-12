import { type ClassValue, clsx } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

/**
 * Tailwind is configured from `tokens.css`, so `tailwind-merge` has to be told
 * about the custom scales it does not ship with — otherwise `cn('text-notes',
 * 'text-text')` would read `text-notes` as an unknown text *color* and silently
 * drop the notes font size.
 */
const twMergeTokens = extendTailwindMerge({
  extend: {
    classGroups: {
      // design.md section 4 — type scale.
      'font-size': [{ text: ['page', 'section', 'body', 'label', 'notes', 'code'] }],
      // design.md section 5 — layout sizing.
      'max-w': [{ 'max-w': ['page', 'modal'] }],
      h: [{ h: ['topbar', 'control', 'row'] }],
      rounded: [{ rounded: ['', 'modal', 'pill'] }],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMergeTokens(clsx(inputs))
}
