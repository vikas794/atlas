import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCheck,
  CloudUpload,
  Eraser,
  ExternalLink,
  Loader,
  Play,
  RefreshCw,
  RotateCcw,
  RotateCw,
  Search,
  Trash2,
} from 'lucide-react'

import {
  approveAllVideos,
  cleanupPlaylistTranscripts,
  deletePlaylist,
  getPlaylist,
  ingestPlaylist,
  processPlaylist,
  reprocessPlaylist,
} from '../../lib/api'
import { failureMessage, isNotFound } from '../../lib/errors'
import { formatPosition, pluralize } from '../../lib/format'
import { pollInterval } from '../../lib/polling'
import { filterCount, type StageFilter } from '../../lib/progress'
import { invalidatePlaylist, queryKeys } from '../../lib/query-keys'
import { onRowKeyDown } from '../../lib/row-keys'
import { isRetriesExhausted, isRetryScheduled, videoDetail } from '../../lib/stage-badge'
import { TONE_TEXT } from '../../lib/tone'
import type { Playlist, Video, VideoListResponse } from '../../lib/types'
import { useDebouncedValue } from '../../lib/use-debounced'
import { fetchPlaylistVideos } from '../../lib/video-queries'
import { cn } from '../../lib/utils'
import { ActionMenu } from '../../components/shared/action-menu'
import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { DisabledReason } from '../../components/shared/disabled-reason'
import { RelativeTime } from '../../components/shared/relative-time'
import { SegmentedProgressBar } from '../../components/shared/segmented-progress-bar'
import {
  EmptyState,
  ErrorState,
  SkeletonBar,
  SkeletonTableRows,
} from '../../components/shared/state-panels'
import { StatusBadge } from '../../components/shared/status-badge'
import { useToast } from '../../components/shared/toast'
import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { buttonClasses } from '../../components/ui/button-styles'
import { ClearTranscriptDialog } from './clear-transcript-dialog'
import { usePublishJob } from './use-publish-job'
import { useRetryVideo } from './use-retry-video'

const PAGE_SIZE = 50
const SEARCH_DEBOUNCE_MS = 300

/** Summary strip cards, in the order of design.md 6.2. */
const STRIP_CARDS: { filter: StageFilter; label: string; tone: string }[] = [
  { filter: 'all', label: 'All videos', tone: 'border-border text-text' },
  { filter: 'queued', label: 'Queued', tone: 'border-border text-st-neutral-fg' },
  {
    filter: 'in_progress',
    label: 'In progress',
    tone: 'border-st-progress-fg/40 text-st-progress-fg',
  },
  {
    filter: 'pending_review',
    label: 'Needs review',
    tone: 'border-st-review-fg/50 bg-st-review-bg/40 text-st-review-fg',
  },
  { filter: 'approved', label: 'Approved', tone: 'border-st-success-fg/40 text-st-success-fg' },
  { filter: 'published', label: 'Published', tone: 'border-st-success-fg/40 text-st-success-fg' },
  {
    filter: 'failed',
    label: 'Failed',
    tone: 'border-st-error-fg/40 bg-st-error-bg/30 text-st-error-fg',
  },
]

/** New videos found by a re-sync: video_count difference, never negative. */
function newVideoCount(before: Playlist, after: Playlist): number {
  return Math.max(0, after.video_count - before.video_count)
}

export function PlaylistDetail() {
  const { id } = useParams<{ id: string }>()
  const playlistId = Number(id)
  const validId = Number.isInteger(playlistId) && playlistId > 0
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [filter, setFilter] = useState<StageFilter>('all')
  const [searchInput, setSearchInput] = useState('')
  const search = useDebouncedValue(searchInput.trim(), SEARCH_DEBOUNCE_MS)
  const [page, setPage] = useState(1)

  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmReprocess, setConfirmReprocess] = useState(false)
  const [confirmCleanup, setConfirmCleanup] = useState(false)
  const [confirmApproveAll, setConfirmApproveAll] = useState(false)
  const [clearTarget, setClearTarget] = useState<Video | null>(null)

  // 1. Playlist metadata and per-stage counts.
  const playlistQuery = useQuery({
    queryKey: queryKeys.playlist(playlistId),
    queryFn: () => getPlaylist(playlistId),
    enabled: validId,
    refetchInterval: (query) => {
      const cachedPages = queryClient.getQueriesData<VideoListResponse>({
        queryKey: queryKeys.videos(playlistId),
      })
      return pollInterval({
        playlist: query.state.data,
        videos: cachedPages.flatMap(([, data]) => data?.videos ?? []),
      })
    },
  })
  const playlist = playlistQuery.data

  // 2. The current page of videos for the active filter and title search.
  const videosQuery = useQuery({
    queryKey: queryKeys.videosPage(playlistId, filter, search, page),
    queryFn: () => fetchPlaylistVideos(playlistId, { filter, search, page, pageSize: PAGE_SIZE }),
    enabled: validId,
    // Keep the old rows on screen while a new filter/search/page loads (no "Loading" flash).
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      pollInterval({
        playlist: queryClient.getQueryData<Playlist>(queryKeys.playlist(playlistId)),
        videos: query.state.data?.videos,
      }),
  })

  // Publish handles both sync (200) and async (202) backends, polling while it runs.
  const publishJob = usePublishJob(playlistId)
  const isPublishing = publishJob.isPublishing || playlist?.status === 'publishing'

  const refresh = () => invalidatePlaylist(queryClient, playlistId)

  const processMut = useMutation({
    mutationFn: () => processPlaylist(playlistId),
    onSuccess: () => {
      toast('Processing started.')
      void refresh()
    },
    onError: (err) => toast(failureMessage('Could not start processing', err), 'error'),
  })

  const approveAllMut = useMutation({
    mutationFn: () => approveAllVideos(playlistId),
    onSuccess: (res) => {
      toast(`Approved ${pluralize(res.count ?? 0, 'video')}.`)
      void refresh()
    },
    onError: (err) => toast(failureMessage('Could not approve the videos', err), 'error'),
  })

  const resyncMut = useMutation({
    mutationFn: (target: Playlist) =>
      ingestPlaylist(target.source_url).then((updated) => ({ updated, target })),
    onSuccess: ({ updated, target }) => {
      const added = newVideoCount(target, updated)
      toast(
        added > 0
          ? `Synced "${target.title}": ${pluralize(added, 'new video')}.`
          : `Synced "${target.title}": no new videos.`,
      )
      void refresh()
    },
    onError: (err) => toast(failureMessage('Could not re-sync the playlist', err), 'error'),
  })

  const reprocessMut = useMutation({
    mutationFn: () => reprocessPlaylist(playlistId),
    onSuccess: () => {
      toast('Playlist queued for reprocessing.')
      void refresh()
    },
    onError: (err) => toast(failureMessage('Could not reprocess the playlist', err), 'error'),
  })

  const deleteMut = useMutation({
    mutationFn: () => deletePlaylist(playlistId),
    onSuccess: () => {
      toast('Playlist deleted.')
      queryClient.removeQueries({ queryKey: queryKeys.playlist(playlistId) })
      queryClient.removeQueries({ queryKey: queryKeys.videos(playlistId) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.playlists })
      navigate('/')
    },
    onError: (err) => toast(failureMessage('Could not delete the playlist', err), 'error'),
  })

  const cleanupMut = useMutation({
    mutationFn: () => cleanupPlaylistTranscripts(playlistId),
    onSuccess: (res) => {
      toast(res.message || 'Transcripts cleared.')
      void refresh()
    },
    onError: (err) => toast(failureMessage('Could not clean up the transcripts', err), 'error'),
  })

  const retryMut = useRetryVideo(playlistId)

  // ---- Loading / error / not found states -------------------------------------------------
  if (!validId || (playlistQuery.isError && isNotFound(playlistQuery.error))) {
    return (
      <div className="space-y-3">
        <h1 className="text-page text-text">Playlist not found</h1>
        <EmptyState title="This playlist does not exist or was deleted.">
          <Link to="/" className="text-accent underline underline-offset-4">
            Back to playlists
          </Link>
        </EmptyState>
      </div>
    )
  }

  if (playlistQuery.isError && !playlist) {
    return (
      <div className="space-y-3">
        <h1 className="text-page text-text">Playlist</h1>
        <ErrorState
          title="Could not load this playlist"
          message={playlistQuery.error.message}
          onRetry={() => void playlistQuery.refetch()}
        />
      </div>
    )
  }

  if (!playlist) {
    return (
      <div className="space-y-5" aria-busy="true">
        <h1 className="sr-only">Playlist</h1>
        <SkeletonBar className="h-6 w-64" />
        <SkeletonBar className="w-96" />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          {STRIP_CARDS.map((card) => (
            <div key={card.filter} className="h-row rounded border border-border bg-surface" />
          ))}
        </div>
        <div className="rounded border border-border bg-surface">
          <table className="w-full">
            <tbody>
              <SkeletonTableRows
                rows={6}
                widths={['w-8', 'w-64', 'w-24', 'w-32', 'w-16', 'w-16']}
              />
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  const counts = playlist.stage_counts ?? {}
  const pendingCount = counts.pending_review ?? 0
  const approvedCount = counts.approved ?? 0
  const publishedCount = counts.published ?? 0
  const queuedCount = (counts.queued ?? 0) + (counts.transcript_ready ?? 0)

  const videos = videosQuery.data?.videos
  const totalVideos = videosQuery.data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(totalVideos / PAGE_SIZE))
  const firstShown = totalVideos === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const lastShown = Math.min(page * PAGE_SIZE, totalVideos)

  const processReason = queuedCount === 0 ? 'No videos are queued' : null
  const approveReason = pendingCount === 0 ? 'No videos are waiting for review' : null
  const publishReason = isPublishing
    ? 'Publishing is already running'
    : approvedCount === 0 && publishedCount === 0
      ? 'No approved videos to publish'
      : null

  const openReview = (video: Video) => navigate(`/playlists/${playlistId}/review/${video.id}`)

  return (
    <div className="space-y-5">
      {/* 6.2 Header */}
      <div className="flex flex-col gap-4 border-b border-border pb-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="truncate text-page text-text">{playlist.title}</h1>
            <a
              href={playlist.source_url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-text-muted transition hover:text-accent"
              aria-label="Open playlist on YouTube"
              title="Open playlist on YouTube"
            >
              <ExternalLink aria-hidden="true" className="size-icon" />
            </a>
          </div>
          <p className="mt-1 text-label text-text-muted">
            <code className="font-mono">{playlist.playlist_id}</code>
            {' · '}
            <RelativeTime
              iso={playlist.last_synced_at}
              prefix="Last synced"
              fallback="Never synced"
            />
            {playlist.status === 'publish_failed' && playlist.last_publish_error ? (
              <span className="text-st-error-fg">
                {' · '}Last publish failed: {playlist.last_publish_error}
              </span>
            ) : null}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <DisabledReason reason={processMut.isPending ? null : processReason}>
            <Button
              variant="secondary"
              onClick={() => processMut.mutate()}
              disabled={processMut.isPending || processReason !== null}
            >
              <Play aria-hidden="true" className="size-icon" />
              Process
            </Button>
          </DisabledReason>

          <DisabledReason reason={approveAllMut.isPending ? null : approveReason}>
            <Button
              variant="secondary"
              onClick={() => setConfirmApproveAll(true)}
              disabled={approveAllMut.isPending || approveReason !== null}
            >
              <CheckCheck aria-hidden="true" className="size-icon" />
              {pendingCount > 0 ? `Approve all (${pendingCount})` : 'Approve all'}
            </Button>
          </DisabledReason>

          <DisabledReason reason={publishReason}>
            <Button
              variant="primary"
              onClick={() => publishJob.publish(publishedCount)}
              disabled={publishReason !== null}
            >
              {isPublishing ? (
                <Loader aria-hidden="true" className="size-icon atlas-spin" />
              ) : (
                <CloudUpload aria-hidden="true" className="size-icon" />
              )}
              {isPublishing ? 'Publishing…' : 'Publish approved to Drive'}
            </Button>
          </DisabledReason>

          <ActionMenu
            label="More actions"
            actions={[
              {
                key: 'resync',
                label: 'Re-sync new videos',
                icon: RefreshCw,
                onSelect: () => resyncMut.mutate(playlist),
                disabled: resyncMut.isPending,
                disabledReason: 'A sync is already running',
              },
              {
                key: 'reprocess',
                label: 'Reprocess',
                icon: RotateCcw,
                onSelect: () => setConfirmReprocess(true),
              },
              {
                key: 'cleanup',
                label: 'Clean transcripts',
                icon: Eraser,
                onSelect: () => setConfirmCleanup(true),
              },
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                danger: true,
                onSelect: () => setConfirmDelete(true),
              },
            ]}
          />
        </div>
      </div>

      {/* 6.2 Summary strip: every card counts the same stages its filter selects. */}
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          {STRIP_CARDS.map((card) => {
            const selected = filter === card.filter
            return (
              <button
                type="button"
                key={card.filter}
                aria-pressed={selected}
                onClick={() => {
                  setFilter(card.filter)
                  setPage(1)
                }}
                className={cn(
                  'flex flex-col items-start rounded border p-3 text-left transition',
                  card.tone,
                  selected ? 'ring-2 ring-accent' : 'hover:bg-surface-muted',
                )}
              >
                <span className="text-label">{card.label}</span>
                <span className="mt-1 text-section tabular-nums">
                  {filterCount(counts, card.filter, playlist.video_count)}
                </span>
              </button>
            )
          })}
        </div>

        <div className="rounded border border-border bg-surface p-3">
          <SegmentedProgressBar counts={counts} total={playlist.video_count} />
        </div>
      </div>

      {/* 6.2 Search row */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full max-w-sm">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-2 size-icon text-text-muted"
          />
          <input
            type="search"
            value={searchInput}
            onChange={(event) => {
              setSearchInput(event.target.value)
              setPage(1)
            }}
            aria-label="Search videos by title"
            placeholder="Search videos by title"
            className="input pl-6"
          />
        </div>

        <p className="text-label tabular-nums text-text-muted" aria-live="polite">
          {totalVideos === 0 ? 'No videos' : `Showing ${firstShown}–${lastShown} of ${totalVideos}`}
        </p>
      </div>

      {/* 6.2 Videos table */}
      {videosQuery.isError && !videos ? (
        <ErrorState
          title="Could not load videos"
          message={videosQuery.error.message}
          onRetry={() => void videosQuery.refetch()}
        />
      ) : (
        <div
          className={cn(
            'max-h-[70vh] overflow-auto rounded border border-border bg-surface transition-opacity',
            videosQuery.isPlaceholderData && 'opacity-60',
          )}
        >
          <table className="w-full text-left text-body">
            <thead className="text-label text-text">
              <tr className="h-row">
                {[
                  ['#', 'w-16 text-right'],
                  ['Title', ''],
                  ['Stage', ''],
                  ['Detail', ''],
                  ['Updated', 'whitespace-nowrap'],
                  ['Actions', 'text-right'],
                ].map(([label, extra]) => (
                  <th
                    key={label}
                    scope="col"
                    className={cn(
                      'sticky top-0 z-10 border-b border-border bg-surface-muted px-3',
                      extra,
                    )}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!videos ? (
                <SkeletonTableRows
                  rows={8}
                  widths={['w-8', 'w-64', 'w-24', 'w-32', 'w-16', 'w-16']}
                />
              ) : videos.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-5 text-center text-text-muted">
                    {search || filter !== 'all'
                      ? 'No videos match this filter.'
                      : 'No videos in this playlist yet.'}
                  </td>
                </tr>
              ) : (
                videos.map((video) => {
                  const needsReview = video.stage === 'pending_review'
                  const isFailed = video.stage === 'failed'
                  const detail = videoDetail(video)

                  return (
                    <tr
                      key={video.id}
                      tabIndex={0}
                      onClick={() => openReview(video)}
                      onKeyDown={(event) => onRowKeyDown(event, () => openReview(video))}
                      className="h-row cursor-pointer border-b border-border transition last:border-b-0 hover:bg-surface-muted"
                    >
                      <td
                        className={cn(
                          'border-l-2 px-3 py-1 text-right font-mono text-label tabular-nums text-text-muted',
                          needsReview ? 'border-l-st-review-fg' : 'border-l-transparent',
                        )}
                      >
                        {formatPosition(video.position)}
                      </td>
                      <td className="max-w-md px-3 py-1 text-text">
                        <Link
                          to={`/playlists/${playlistId}/review/${video.id}`}
                          onClick={(event) => event.stopPropagation()}
                          className="block truncate hover:text-accent hover:underline"
                        >
                          {video.title}
                        </Link>
                      </td>
                      <td className="px-3 py-1">
                        <StatusBadge
                          stage={video.stage}
                          failureKind={video.failure_kind}
                          retryScheduled={isRetryScheduled(video)}
                          removedUpstream={video.removed_upstream}
                        />
                      </td>
                      <td className="px-3 py-1 text-label">
                        {detail.reason ? (
                          <span
                            className={cn(
                              'block',
                              TONE_TEXT[detail.tone === 'muted' ? 'neutral' : detail.tone],
                            )}
                          >
                            {detail.reason}
                          </span>
                        ) : null}
                        {detail.note ? (
                          <span
                            className={cn(
                              'block',
                              TONE_TEXT[detail.tone === 'muted' ? 'neutral' : detail.tone],
                            )}
                          >
                            {detail.note}
                          </span>
                        ) : null}
                        {detail.edited ? <Badge tone="neutral">Edited</Badge> : null}
                        {!detail.reason && !detail.note && !detail.edited ? (
                          <span className="text-text-muted">{'–'}</span>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap px-3 py-1 text-label text-text-muted">
                        <RelativeTime iso={video.updated_at} />
                      </td>
                      <td className="px-3 py-1">
                        <div className="flex items-center justify-end gap-2">
                          {isFailed && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={(event) => {
                                event.stopPropagation()
                                retryMut.mutate(video)
                              }}
                              disabled={retryMut.isPending}
                              aria-label={`Retry #${formatPosition(video.position)}`}
                              title={
                                isRetriesExhausted(video)
                                  ? 'Retries exhausted. Retry now.'
                                  : 'Retry this video'
                              }
                            >
                              <RotateCw aria-hidden="true" className="size-icon" />
                              Retry
                            </Button>
                          )}
                          <Link
                            to={`/playlists/${playlistId}/review/${video.id}`}
                            onClick={(event) => event.stopPropagation()}
                            aria-label={`Review #${formatPosition(video.position)}`}
                            className={buttonClasses({ variant: 'ghost', size: 'sm' })}
                          >
                            Review
                          </Link>
                          <ActionMenu
                            label={`More actions for #${formatPosition(video.position)}`}
                            actions={[
                              {
                                key: 'clear-transcript',
                                label: 'Clear transcript',
                                icon: Eraser,
                                danger: true,
                                onSelect: () => setClearTarget(video),
                              },
                            ]}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination (50 per page) */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-border pt-4">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={page === 1}
          >
            Previous
          </Button>
          <span className="text-label tabular-nums text-text-muted">
            Page {page} of {totalPages}
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            disabled={page === totalPages}
          >
            Next
          </Button>
        </div>
      )}

      {/* Confirmation dialogs: consequence and count (design.md 6.4) */}
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete playlist"
        description={`Delete "${playlist.title}" and all ${pluralize(playlist.video_count, 'stored video')}, transcripts and notes? This does not delete Google Docs already in Drive.`}
        confirmLabel="Delete playlist"
        isDestructive
        isLoading={deleteMut.isPending}
        onConfirm={() => deleteMut.mutateAsync()}
      />

      <ConfirmDialog
        open={confirmReprocess}
        onOpenChange={setConfirmReprocess}
        title="Reprocess playlist"
        description={`Reprocess "${playlist.title}"? All ${pluralize(playlist.video_count, 'video')} go back to the queue and their transcripts and notes are discarded and regenerated, including your edits and approvals. Google Docs already in Drive are not deleted.`}
        confirmLabel="Reprocess playlist"
        isDestructive
        isLoading={reprocessMut.isPending}
        onConfirm={() => reprocessMut.mutateAsync()}
      />

      <ConfirmDialog
        open={confirmCleanup}
        onOpenChange={setConfirmCleanup}
        title="Clean up transcripts"
        description={`Delete the stored transcripts of all ${pluralize(playlist.video_count, 'video')} in "${playlist.title}"? Generated notes are kept, but regenerating notes will need the transcripts fetched again.`}
        confirmLabel="Clean up transcripts"
        isDestructive
        isLoading={cleanupMut.isPending}
        onConfirm={() => cleanupMut.mutateAsync()}
      />

      <ConfirmDialog
        open={confirmApproveAll}
        onOpenChange={setConfirmApproveAll}
        title="Approve all"
        description={`Approve all ${pluralize(pendingCount, 'video')} waiting for review? They become ready to publish to Drive. You can still edit them afterwards.`}
        confirmLabel={`Approve all (${pendingCount})`}
        isDestructive={false}
        isLoading={approveAllMut.isPending}
        onConfirm={() => approveAllMut.mutateAsync()}
      />

      <ClearTranscriptDialog video={clearTarget} onClose={() => setClearTarget(null)} />
    </div>
  )
}
