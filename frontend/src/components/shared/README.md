# components/shared

## Overview
Reusable, app-aware building blocks used by multiple feature screens (dialogs, status badges, progress bars, empty/error states, toasts).

## Interface
- Dialogs and menus: `ConfirmDialog`, `FormDialog`, `Field`, `ActionMenu`, `MenuAction`, `DisabledReason`.
- Status and progress: `StatusBadge`, `StageDot`, `KeyStatusBadge`, `KeyRowBadge`, `SegmentedProgressBar`, `StagedBar`, `Meter`, `StatCard`, `CountUp`.
- Layout and content: `PageHeader`, `ProviderMark`, `RelativeTime`, `MarkdownBody`.
- States and errors: `EmptyState`, `ErrorState`, `SkeletonBar`, `SkeletonTableRows`, `SkeletonListRows`, `NotFound`, `ErrorBoundary`, `RouteErrorBoundary`, `ErrorFallback`.
- Toasts: `ToastProvider`, `useToast`, `Toast`, `ToastType`, `TOAST_DISMISS_MS`.

## Invariants
- Badge appearance is derived from `lib/stage-badge`, `lib/key-status-badge` and `lib/tone`, not decided locally.
- Components stay free of API calls; data arrives through props.
- Colors come from global tokens (`tokens.css`).

## Side Effects
- `ToastProvider` owns timers that auto-dismiss toasts.
- `RelativeTime` re-renders on a shared clock (`lib/use-now`); `CountUp` animates and respects `lib/motion` (`motionAllowed`).

## Verification
- Run: `npm test` (from `frontend/`)
