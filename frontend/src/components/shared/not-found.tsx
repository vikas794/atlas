import { Link } from 'react-router-dom'

import { EmptyState } from './state-panels'

/** Unknown route: say so and offer the way home. */
export function NotFound() {
  return (
    <div className="space-y-3">
      <h1 className="text-page text-text">Page not found</h1>
      <EmptyState title="That page does not exist.">
        <Link to="/" className="text-accent underline underline-offset-4">
          Back to playlists
        </Link>
      </EmptyState>
    </div>
  )
}
