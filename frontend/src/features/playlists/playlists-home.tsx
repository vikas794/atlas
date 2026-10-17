import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Plus, RefreshCw, RotateCcw, RotateCw, Trash2 } from 'lucide-react'

import {
  deletePlaylist,
  getPlaylists,
  ingestPlaylist,
  reprocessPlaylist,
  retryAllFailedVideos,
} from '../../lib/api'
import { errorMessage, failureMessage } from '../../lib/errors'
import { pluralize } from '../../lib/format'
import { playlistIsBusy, pollInterval } from '../../lib/polling'
import { validatePlaylistUrl } from '../../lib/playlist-url'
import { queryKeys } from '../../lib/query-keys'
import { onRowKeyDown } from '../../lib/row-keys'
import type { Playlist } from '../../lib/types'
import { ActionMenu } from '../../components/shared/action-menu'
import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { DisabledReason } from '../../components/shared/disabled-reason'
import { RelativeTime } from '../../components/shared/relative-time'
import { SegmentedProgressBar } from '../../components/shared/segmented-progress-bar'
import { EmptyState, ErrorState, SkeletonTableRows } from '../../components/shared/state-panels'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'

export function PlaylistsHome() {
  const [url, setUrl] = useState('')
  const [urlError, setUrlError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Playlist | null>(null)
  const [reprocessTarget, setReprocessTarget] = useState<Playlist | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { toast } = useToast()

  const playlistsQuery = useQuery({
    queryKey: queryKeys.playlists,
    queryFn: () => getPlaylists(),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.playlists.some((playlist) => playlistIsBusy(playlist))
        ? pollInterval({ asyncRunning: true })
        : pollInterval({}),
  })

  const playlists = playlistsQuery.data?.playlists
  const isEmpty = playlistsQuery.isSuccess && playlists?.length === 0

  // design.md 6.1: with nothing tracked yet the input is focused.
  useEffect(() => {
    if (isEmpty) inputRef.current?.focus()
  }, [isEmpty])

  const invalidateLists = () => queryClient.invalidateQueries({ queryKey: queryKeys.playlists })

  const ingestMutation = useMutation({
    mutationFn: (playlistUrl: string) => ingestPlaylist(playlistUrl),
    onSuccess: (playlist) => {
      setUrl('')
      setUrlError(null)
      // A known link is re-synced by the backend (HTTP 200), not added again.
      const known = playlistsQuery.data?.playlists.find((item) => item.id === playlist.id)
      toast(
        known
          ? `"${playlist.title}" is already tracked: synced, ${pluralize(Math.max(0, playlist.video_count - known.video_count), 'new video')}.`
          : `Added "${playlist.title}" with ${pluralize(playlist.video_count, 'video')}.`,
      )
      void invalidateLists()
    },
    onError: (err) => {
      // Server-side errors (private playlist, bad URL...) show inline and as a toast.
      setUrlError(errorMessage(err, 'Could not add this playlist.'))
      toast(failureMessage('Could not add the playlist', err), 'error')
    },
  })

  // Re-sync is its own mutation: it must not touch the add-playlist input or
  // announce "added".
  const resyncMutation = useMutation({
    mutationFn: ({ playlist }: { playlist: Playlist }) => ingestPlaylist(playlist.source_url),
    onSuccess: (updated, { playlist }) => {
      const added = Math.max(0, updated.video_count - playlist.video_count)
      toast(
        added > 0
          ? `Synced "${playlist.title}": ${pluralize(added, 'new video')}.`
          : `Synced "${playlist.title}": no new videos.`,
      )
      void queryClient.invalidateQueries({ queryKey: queryKeys.playlist(playlist.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.videos(playlist.id) })
      void invalidateLists()
    },
    onError: (err, { playlist }) => {
      toast(failureMessage(`Could not sync "${playlist.title}"`, err), 'error')
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (playlist: Playlist) => deletePlaylist(playlist.id),
    onSuccess: (_result, playlist) => {
      toast(`Deleted "${playlist.title}".`)
      queryClient.removeQueries({ queryKey: queryKeys.playlist(playlist.id) })
      queryClient.removeQueries({ queryKey: queryKeys.videos(playlist.id) })
      void invalidateLists()
    },
    onError: (err) => toast(failureMessage('Could not delete the playlist', err), 'error'),
  })

  const reprocessMutation = useMutation({
    mutationFn: (playlist: Playlist) => reprocessPlaylist(playlist.id),
    onSuccess: (_result, playlist) => {
      toast(`Reprocessing "${playlist.title}" from scratch.`)
      void queryClient.invalidateQueries({ queryKey: queryKeys.playlist(playlist.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.videos(playlist.id) })
      void invalidateLists()
    },
    onError: (err) => toast(failureMessage('Could not reprocess the playlist', err), 'error'),
  })

  // The failed videos can sit in any playlist, so detail caches are invalidated by prefix.
  const retryAllMutation = useMutation({
    mutationFn: retryAllFailedVideos,
    onSuccess: (result) => {
      const count = result.count ?? 0
      toast(
        count > 0
          ? `Queued ${pluralize(count, 'failed video')} for retry.`
          : 'No failed videos to retry.',
      )
      void queryClient.invalidateQueries({ queryKey: ['playlist'] })
      void queryClient.invalidateQueries({ queryKey: ['playlist-videos'] })
      void invalidateLists()
    },
    onError: (err) => toast(failureMessage('Could not retry the failed videos', err), 'error'),
  })

  const failedTotal = (playlists ?? []).reduce(
    (sum, playlist) => sum + (playlist.stage_counts?.failed ?? 0),
    0,
  )

  const handleAddSubmit =(event: FormEvent) => {
    event.preventDefault()
    const message = validatePlaylistUrl(url)
    if (message) {
      setUrlError(message)
      inputRef.current?.focus()
      return
    }
    setUrlError(null)
    ingestMutation.mutate(url.trim())
  }

  const openPlaylist = (playlist: Playlist) => navigate(`/playlists/${playlist.id}`)

  return (
    <div className="space-y-5">
      <h1 className="text-page text-text">Playlists</h1>

      {/* 6.1 Top: ingestion form */}
      <section className="rounded border border-border bg-surface p-4">
        <h2 className="text-section text-text">Add YouTube playlist</h2>
        <p className="mt-1 text-body text-text-muted">
          Paste a YouTube playlist link to fetch its videos and generate study notes.
        </p>

        <form
          onSubmit={handleAddSubmit}
          noValidate
          className="mt-4 flex flex-col gap-2 sm:flex-row"
        >
          <div className="flex-1">
            <input
              ref={inputRef}
              type="text"
              value={url}
              aria-label="Paste a YouTube playlist link"
              aria-invalid={urlError ? true : undefined}
              aria-describedby={urlError ? 'playlist-url-error' : undefined}
              onChange={(event) => {
                setUrl(event.target.value)
                if (urlError) setUrlError(null)
              }}
              placeholder="Paste a YouTube playlist link"
              className="input"
              disabled={ingestMutation.isPending}
            />
            {urlError && (
              <p id="playlist-url-error" role="alert" className="mt-1 text-label text-st-error-fg">
                {urlError}
              </p>
            )}
          </div>
          <Button type="submit" disabled={ingestMutation.isPending} className="shrink-0">
            <Plus aria-hidden="true" className="size-icon" />
            {ingestMutation.isPending ? 'Adding…' : 'Add playlist'}
          </Button>
        </form>
      </section>

      {/* 6.1 Below: playlists table */}
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-section text-text">Tracked playlists</h2>
          {playlists ? (
            <div className="flex items-center gap-3">
              <span className="text-label tabular-nums text-text-muted">
                {pluralize(playlists.length, 'playlist')}
              </span>
              <DisabledReason reason={failedTotal === 0 ? 'No failed videos' : null}>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={failedTotal === 0 || retryAllMutation.isPending}
                  onClick={() => retryAllMutation.mutate()}
                >
                  <RotateCw aria-hidden="true" className="size-icon" />
                  {retryAllMutation.isPending ? 'Retrying…' : `Retry all failed (${failedTotal})`}
                </Button>
              </DisabledReason>
            </div>
          ) : null}
        </div>

        {playlistsQuery.isError && !playlists ? (
          <ErrorState
            title="Could not load playlists"
            message={playlistsQuery.error.message}
            onRetry={() => void playlistsQuery.refetch()}
          />
        ) : isEmpty ? (
          <EmptyState title="No playlists yet.">
            Paste a YouTube playlist link above to start generating notes.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto rounded border border-border bg-surface">
            <table className="w-full text-left text-body">
              <thead className="border-b border-border bg-surface-muted text-label text-text">
                <tr className="h-row">
                  <th scope="col" className="px-3">
                    Title
                  </th>
                  <th scope="col" className="px-3 text-right">
                    Videos
                  </th>
                  <th scope="col" className="min-w-48 px-3">
                    Progress
                  </th>
                  <th scope="col" className="px-3 text-right">
                    Needs review
                  </th>
                  <th scope="col" className="px-3 text-right">
                    Failed
                  </th>
                  <th scope="col" className="px-3">
                    Last synced
                  </th>
                  <th scope="col" className="w-12 px-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {!playlists ? (
                  <SkeletonTableRows
                    rows={4}
                    widths={['w-48', 'w-8', 'w-48', 'w-8', 'w-8', 'w-16', 'w-8']}
                  />
                ) : (
                  playlists.map((playlist) => {
                    const counts = playlist.stage_counts ?? {}
                    const needsReview = counts.pending_review ?? 0
                    const failed = counts.failed ?? 0

                    return (
                      <tr
                        key={playlist.id}
                        tabIndex={0}
                        onClick={() => openPlaylist(playlist)}
                        onKeyDown={(event) => onRowKeyDown(event, () => openPlaylist(playlist))}
                        className="h-row cursor-pointer border-b border-border transition last:border-b-0 hover:bg-surface-muted"
                      >
                        <td className="px-3 py-1 text-text">
                          <Link
                            to={`/playlists/${playlist.id}`}
                            onClick={(event) => event.stopPropagation()}
                            className="hover:text-accent hover:underline"
                          >
                            {playlist.title || 'Untitled playlist'}
                          </Link>
                        </td>
                        <td className="px-3 py-1 text-right tabular-nums text-text-muted">
                          {playlist.video_count}
                        </td>
                        <td className="px-3 py-1">
                          <SegmentedProgressBar counts={counts} total={playlist.video_count} />
                        </td>
                        <td className="px-3 py-1 text-right tabular-nums">
                          <span
                            className={needsReview > 0 ? 'text-st-review-fg' : 'text-text-muted'}
                          >
                            {needsReview}
                          </span>
                        </td>
                        <td className="px-3 py-1 text-right tabular-nums">
                          <span className={failed > 0 ? 'text-st-error-fg' : 'text-text-muted'}>
                            {failed}
                          </span>
                        </td>
                        <td className="px-3 py-1 text-text-muted">
                          <RelativeTime iso={playlist.last_synced_at} fallback="Never" />
                        </td>
                        <td className="px-3 py-1 text-right">
                          <ActionMenu
                            label={`Actions for ${playlist.title || 'playlist'}`}
                            actions={[
                              {
                                key: 'open',
                                label: 'Open',
                                icon: ExternalLink,
                                onSelect: () => openPlaylist(playlist),
                              },
                              {
                                key: 'resync',
                                label: 'Re-sync new videos',
                                icon: RefreshCw,
                                onSelect: () => resyncMutation.mutate({ playlist }),
                                disabled: resyncMutation.isPending,
                                disabledReason: 'A sync is already running',
                              },
                              {
                                key: 'reprocess',
                                label: 'Reprocess',
                                icon: RotateCcw,
                                onSelect: () => setReprocessTarget(playlist),
                              },
                              {
                                key: 'delete',
                                label: 'Delete',
                                icon: Trash2,
                                danger: true,
                                onSelect: () => setDeleteTarget(playlist),
                              },
                            ]}
                          />
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete playlist"
        description={`Delete "${deleteTarget?.title}" and all ${pluralize(deleteTarget?.video_count ?? 0, 'stored video')}, transcripts and notes? This does not delete Google Docs already in Drive.`}
        confirmLabel="Delete playlist"
        isDestructive
        onConfirm={() => (deleteTarget ? deleteMutation.mutateAsync(deleteTarget) : undefined)}
      />

      <ConfirmDialog
        open={Boolean(reprocessTarget)}
        onOpenChange={(open) => !open && setReprocessTarget(null)}
        title="Reprocess playlist"
        description={`Reprocess "${reprocessTarget?.title}"? All ${pluralize(reprocessTarget?.video_count ?? 0, 'video')} go back to the queue and their transcripts and notes are discarded and regenerated, including your edits and approvals. Google Docs already in Drive are not deleted.`}
        confirmLabel="Reprocess playlist"
        isDestructive
        onConfirm={() =>
          reprocessTarget ? reprocessMutation.mutateAsync(reprocessTarget) : undefined
        }
      />
    </div>
  )
}
