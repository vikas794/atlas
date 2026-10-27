# components/ui

## Overview
Low-level, presentation-only primitives styled with the token-based Tailwind setup.

## Interface
- `Button`, `buttonClasses`, `ButtonVariant`, `ButtonSize` (`button.tsx`, `button-styles.ts`) - button component and reusable class builder for link-styled buttons.
- `Badge`, `BadgeTone` - status pill.
- `Card` - surface container.
- `Switch` - toggle control.

## Invariants
- No data fetching or app state; props in, markup out.
- Styling uses design tokens and `cn` from `lib/utils`.

## Side Effects
- None.

## Verification
- Run: `npm test` (from `frontend/`)
