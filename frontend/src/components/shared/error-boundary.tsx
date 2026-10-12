import { AlertCircle } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { isRouteErrorResponse, useRouteError } from 'react-router-dom'

import { errorMessage } from '../../lib/errors'
import { buttonClasses } from '../ui/button-styles'

/**
 * Human-readable fallback for a crashed screen. Plain `<a>` links (not router
 * `<Link>`) so it also works above the router, where no router context exists.
 */
export function ErrorFallback({ error }: { error: unknown }) {
  const detail = errorMessage(error, 'Unknown error.')
  return (
    <div
      role="alert"
      className="mx-auto mt-6 flex max-w-modal flex-col gap-3 rounded border border-st-error-fg bg-surface p-5"
    >
      <h1 className="flex items-center gap-2 text-section text-st-error-fg">
        <AlertCircle aria-hidden="true" className="size-icon-lg shrink-0" />
        Something went wrong on this screen
      </h1>
      <p className="text-body text-text">
        Atlas hit an unexpected error while showing this page. Your playlists, notes and edits that
        were already saved are safe. Reload the page to try again.
      </p>
      <details className="text-label text-text-muted">
        <summary className="cursor-pointer">Technical details</summary>
        <p className="mt-2 break-words font-mono">{detail}</p>
      </details>
      <div className="flex gap-2">
        <button
          type="button"
          className={buttonClasses({ variant: 'primary' })}
          onClick={() => window.location.reload()}
        >
          Reload page
        </button>
        <a href="/" className={buttonClasses({ variant: 'secondary' })}>
          Back to playlists
        </a>
      </div>
    </div>
  )
}

interface ErrorBoundaryState {
  error: unknown
}

/** Last-resort boundary around the whole app (providers and router included). */
export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error: error ?? new Error('Unknown error.') }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('Atlas UI crashed', error, info.componentStack)
  }

  render() {
    return this.state.error ? <ErrorFallback error={this.state.error} /> : this.props.children
  }
}

/**
 * `errorElement` for the routed screens: a render error inside a screen keeps
 * the top bar (and toasts) and replaces only the screen with the fallback.
 */
export function RouteErrorBoundary() {
  const error = useRouteError()
  if (isRouteErrorResponse(error)) {
    return <ErrorFallback error={new Error(`${error.status} ${error.statusText}`)} />
  }
  return <ErrorFallback error={error} />
}
