import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useBlocker, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronRight,
  CloudUpload,
  Eraser,
  ExternalLink,
  Loader,
  RotateCcw,
  RotateCw,
  Save,
} from 'lucide-react'

import {
  approveVideo,
  getPlaylist,
  getVideoSummary,
  regenerateVideo,
  updateVideoSummary,
} from '../../lib/api'
import { errorMessage, failureMessage, isNotFound } from '../../lib/errors'
import { formatPosition } from '../../lib/format'
import { regeneratePhase, type JobWatch } from '../../lib/jobs'
import { pollInterval } from '../../lib/polling'
import { invalidateVideo, queryKeys } from '../../lib/query-keys'
import { shouldRetry } from '../../lib/query-client'
import { findNextNeedingReview } from '../../lib/review-nav'
import { isRetryScheduled, videoBadge, videoDetail } from '../../lib/stage-badge'
import { TONE_TEXT } from '../../lib/tone'
import type { Video } from '../../lib/types'
import { fetchAllPlaylistVideos } from '../../lib/video-queries'
import { cn } from '../../lib/utils'
import { ActionMenu } from '../../components/shared/action-menu'
import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { DisabledReason } from '../../components/shared/disabled-reason'
import { MarkdownBody } from '../../components/shared/markdown-body'
import { RelativeTime } from '../../components/shared/relative-time'
import {
  EmptyState,
  ErrorState,
  SkeletonBar,
  SkeletonListRows,
} from '../../components/shared/state-panels'
import { StageDot, StatusBadge } from '../../components/shared/status-badge'
import { useToast } from '../../components/shared/toast'
import { Button } from '../../components/ui/button'
import { ClearTranscriptDialog } from './clear-transcript-dialog'
import { usePublishJob } from './use-publish-job'
import { useRetryVideo } from './use-retry-video'

type Mode = 'preview' | 'edit'

interface Draft {
  videoId: number
  content: string
}

export function ReviewScreen() {
  const { id, videoId } = useParams<{ id: string; videoId: string }>()
  const playlistId = Number(id)
  const currentVideoPk = Number(videoId)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [mode, setMode] = useState<Mode>('preview')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [clearTarget, setClearTarget] = useState<Video | null>(null)
  const [regenWatch, setRegenWatch] = useState<JobWatch | null>(null)

  // 1. Playlist metadata (breadcrumb, publish counts).
  const { data: playlist } = useQuery({
    queryKey: queryKeys.playlist(playlistId),
    queryFn: () => getPlaylist(playlistId),
    enabled: Boolean(playlistId),
  })

  // 2. Every video of the playlist for the rail and "next needing review". Pages
  //    are fetched until exhausted, so playlists over 250 videos still work.
  const videosQuery = useQuery({
    queryKey: queryKeys.videosAll(playlistId),
    queryFn: () => fetchAllPlaylistVideos(playlistId),
    enabled: Boolean(playlistId),
    refetchInterval: (query) =>
      pollInterval({
        videos: query.state.data,
        asyncRunning: regenWatch !== null,
      }),
  })
  const videos = videosQuery.data
  const currentVideo = videos?.find((video) => video.id === currentVideoPk)

  // 3. Notes of the selected video. A 404 means "not generated yet" and is not retried.
  const summaryQuery = useQuery({
    queryKey: queryKeys.summary(currentVideoPk),
    queryFn: () => getVideoSummary(currentVideoPk),
    enabled: Boolean(currentVideoPk),
    retry: shouldRetry,
  })
  const summary = summaryQuery.data
  const notesMissing = isNotFound(summaryQuery.error)

  const editedContent =
    draft && draft.videoId === currentVideoPk ? draft.content : (summary?.content ?? '')
  const isDirty = Boolean(
    draft && draft.videoId === currentVideoPk && draft.content !== (summary?.content ?? ''),
  )

  // "Edited since publishing": the backend compares the stored notes with the published digest.
  const showRepublish = currentVideo?.stage === 'published' && summary?.needs_republish === true

  // ---- Unsaved changes guard -------------------------------------------------------------
  const dirtyRef = useRef(false)
  useEffect(() => {
    dirtyRef.current = isDirty
  }, [isDirty])

  // Blocks every in-app navigation that leaves this note (rail, Next, breadcrumb, top bar,
  // browser back). Switching videos changes the path, so it is covered too.
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirtyRef.current && currentLocation.pathname !== nextLocation.pathname,
  )
  const discardingRef = useRef(false)

  // Closing the tab or reloading.
  useEffect(() => {
    if (!isDirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [isDirty])

  // ---- Mutations -----------------------------------------------------------------------------
  const saveMut = useMutation({
    mutationFn: ({ videoId: target, content }: Draft) => updateVideoSummary(target, content),
    // `variables.videoId` is the video the save was started for, not the one on screen now.
    onSuccess: (updated, { videoId: savedId }) => {
      dirtyRef.current = false
      setDraft((current) => (current && current.videoId === savedId ? null : current))
      toast('Notes saved.')
      queryClient.setQueryData(queryKeys.summary(savedId), updated)
      void invalidateVideo(queryClient, playlistId, savedId)
    },
    onError: (err) => toast(failureMessage('Could not save the notes', err), 'error'),
  })

  const approveMut = useMutation({
    mutationFn: (target: number) => approveVideo(target),
    onSuccess: (_result, target) => {
      toast('Video approved.')
      void invalidateVideo(queryClient, playlistId, target)
    },
    onError: (err) => toast(failureMessage('Could not approve the video', err), 'error'),
  })

  const regenMut = useMutation({
    mutationFn: (target: number) => regenerateVideo(target).then(() => ({ target })),
    onSuccess: ({ target }) => {
      // Regeneration runs in the background (202): show "Regenerating..." and poll the video.
      setRegenWatch({ startedAt: Date.now() })
      void invalidateVideo(queryClient, playlistId, target)
    },
    onError: (err) => toast(failureMessage('Could not regenerate the notes', err), 'error'),
  })

  const publishJob = usePublishJob(playlistId)
  const retryMut = useRetryVideo(playlistId)

  // Async regenerate finished (or failed): report it and refresh.
  const regenPhase = regeneratePhase(currentVideo, videosQuery.dataUpdatedAt, regenWatch)
  const regenLatest = useRef({ currentVideo, currentVideoPk })
  useEffect(() => {
    regenLatest.current = { currentVideo, currentVideoPk }
  })
  useEffect(() => {
    if (regenPhase !== 'succeeded' && regenPhase !== 'failed') return
    const { currentVideo: video, currentVideoPk: target } = regenLatest.current
    if (regenPhase === 'failed') {
      toast(video?.failure_reason || 'Regenerating the notes failed.', 'error')
    } else {
      toast('Notes regenerated.')
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDraft(null)
    }
    // The async job is over: stop watching.
    setRegenWatch(null)
    void invalidateVideo(queryClient, playlistId, target)
  }, [regenPhase, toast, queryClient, playlistId])
  const isRegenerating = regenMut.isPending || regenPhase === 'running'

  // ---- Actions ---------------------------------------------------------------------------------
  const nextId = videos ? findNextNeedingReview(videos, currentVideoPk) : null

  const goToNextNeedingReview = useCallback(() => {
    if (nextId === null) {
      toast('No more videos need review.', 'info')
      return
    }
    navigate(`/playlists/${playlistId}/review/${nextId}`)
  }, [nextId, navigate, playlistId, toast])

  const save = async () => {
    if (!isDirty || saveMut.isPending) return false
    try {
      await saveMut.mutateAsync({ videoId: currentVideoPk, content: editedContent })
      return true
    } catch {
      return false // toast already shown; the draft is kept
    }
  }

  /** Save the draft first (when dirty), then approve, then go to the next video. */
  const approveAndNext = async () => {
    if (saveMut.isPending || approveMut.isPending) return
    if (isDirty && !(await save())) return
    try {
      await approveMut.mutateAsync(currentVideoPk)
    } catch {
      return
    }
    goToNextNeedingReview()
  }

  // Keyboard shortcuts read live state through a ref, so the listener is bound once.
  const live = useRef({ canApprove: false, busy: false, isDirty: false, save, approveAndNext })
  useEffect(() => {
    live.current = {
      canApprove: currentVideo?.stage === 'pending_review' && Boolean(summary),
      busy: saveMut.isPending || approveMut.isPending,
      isDirty,
      save,
      approveAndNext,
    }
  })
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || !(event.ctrlKey || event.metaKey)) return
      // A dialog (confirm, discard) owns the keyboard while it is open.
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return
      const key = event.key.toLowerCase()
      const state = live.current
      if (key === 's') {
        event.preventDefault()
        if (state.isDirty && !state.busy) void state.save()
      } else if (key === 'enter') {
        event.preventDefault()
        if (state.canApprove && !state.busy) void state.approveAndNext()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  // Keep the active rail row visible for long playlists.
  const activeRowRef = useRef<HTMLAnchorElement>(null)
  useEffect(() => {
    activeRowRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [currentVideoPk, videos?.length])

  const positionLabel = currentVideo ? `#${formatPosition(currentVideo.position)}` : ''
  const isBusy = saveMut.isPending || approveMut.isPending

  const nextReason = nextId === null ? 'No other videos need review' : null
  const detail = currentVideo ? videoDetail(currentVideo) : null

  return (
    <div className="flex h-[calc(100vh-var(--topbar-height)-var(--space-5)*2)] min-h-96 flex-col gap-4">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-label text-text-muted">
        <Link to="/" className="hover:text-text hover:underline">
          Playlists
        </Link>
        <span aria-hidden="true">/</span>
        <Link to={`/playlists/${playlistId}`} className="hover:text-text hover:underline">
          {playlist?.title || 'Playlist'}
        </Link>
        <span aria-hidden="true">/</span>
        <span className="truncate text-text" aria-current="page">
          {currentVideo ? `${positionLabel} ${currentVideo.title}` : 'Review'}
        </span>
      </nav>

      <div className="flex min-h-0 flex-1 gap-4">
        {/* 6.3 Left rail */}
        <aside
          aria-label="Videos in this playlist"
          className="hidden w-64 shrink-0 flex-col rounded border border-border bg-surface md:flex"
        >
          <div className="border-b border-border p-3">
            <Link
              to={`/playlists/${playlistId}`}
              className="inline-flex items-center gap-2 text-label text-text-muted hover:text-text"
            >
              <ArrowLeft aria-hidden="true" className="size-icon" />
              Back to playlist
            </Link>
            <h2 className="mt-2 text-label text-text-muted">
              Videos{videos ? ` (${videos.length})` : ''}
            </h2>
          </div>

          <div className="flex-1 overflow-y-auto">
            {!videos ? (
              videosQuery.isError ? (
                <ErrorState
                  className="m-3"
                  title="Could not load videos"
                  onRetry={() => void videosQuery.refetch()}
                />
              ) : (
                <SkeletonListRows />
              )
            ) : (
              videos.map((video) => {
                const isActive = video.id === currentVideoPk
                const badge = videoBadge(video)
                return (
                  <Link
                    key={video.id}
                    ref={isActive ? activeRowRef : undefined}
                    to={`/playlists/${playlistId}/review/${video.id}`}
                    aria-current={isActive ? 'page' : undefined}
                    className={cn(
                      'flex h-row items-center gap-2 border-b border-l-2 border-border px-3 text-label transition',
                      video.stage === 'pending_review'
                        ? 'border-l-st-review-fg'
                        : 'border-l-transparent',
                      isActive ? 'bg-accent-soft text-text' : 'text-text hover:bg-surface-muted',
                    )}
                  >
                    <StageDot spec={badge} />
                    <span className="shrink-0 font-mono tabular-nums text-text-muted">
                      {formatPosition(video.position)}
                    </span>
                    <span className="truncate">{video.title}</span>
                  </Link>
                )
              })
            )}
          </div>
        </aside>

        {/* 6.3 Right notes pane */}
        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded border border-border bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-section text-text">
                  {currentVideo ? `${positionLabel} ${currentVideo.title}` : 'Review'}
                </h1>
                {currentVideo && (
                  <StatusBadge
                    stage={currentVideo.stage}
                    failureKind={currentVideo.failure_kind}
                    retryScheduled={isRetryScheduled(currentVideo)}
                    removedUpstream={currentVideo.removed_upstream}
                  />
                )}
              </div>

              <div className="mt-1 flex flex-wrap items-center gap-3 text-label text-text-muted">
                {summary?.model && <span>Model: {summary.model}</span>}
                {summary?.prompt_version && <span>Prompt: {summary.prompt_version}</span>}
                {summary?.generated_at && (
                  <RelativeTime iso={summary.generated_at} prefix="Generated" />
                )}
                {currentVideo && (
                  <a
                    href={`https://www.youtube.com/watch?v=${currentVideo.youtube_video_id}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-accent hover:underline"
                  >
                    Open video
                    <ExternalLink aria-hidden="true" className="size-icon" />
                  </a>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2">
              {currentVideo?.stage === 'pending_review' && (
                <DisabledReason reason={!summary ? 'The notes have not loaded yet' : null}>
                  <Button
                    variant="primary"
                    onClick={() => void approveAndNext()}
                    disabled={isBusy || !summary}
                    title="Approve notes (Ctrl+Enter)"
                  >
                    <Check aria-hidden="true" className="size-icon" />
                    {isDirty ? 'Save and approve' : 'Approve'}
                  </Button>
                </DisabledReason>
              )}

              <Button
                variant="secondary"
                onClick={() => setConfirmRegen(true)}
                disabled={isRegenerating || !currentVideo}
                title="Regenerate notes from the stored transcript"
              >
                {isRegenerating ? (
                  <Loader aria-hidden="true" className="size-icon atlas-spin" />
                ) : (
                  <RotateCcw aria-hidden="true" className="size-icon" />
                )}
                {isRegenerating ? 'Regenerating…' : 'Regenerate'}
              </Button>

              <DisabledReason reason={nextReason}>
                <Button
                  variant="secondary"
                  onClick={goToNextNeedingReview}
                  disabled={nextId === null}
                >
                  Next needing review
                  <ChevronRight aria-hidden="true" className="size-icon" />
                </Button>
              </DisabledReason>

              <ActionMenu
                label="More actions"
                actions={[
                  {
                    key: 'clear-transcript',
                    label: 'Clear transcript',
                    icon: Eraser,
                    danger: true,
                    disabled: !currentVideo,
                    onSelect: () => currentVideo && setClearTarget(currentVideo),
                  },
                ]}
              />
            </div>
          </div>

          {/* Failure reason for a failed video */}
          {detail?.reason && currentVideo?.stage === 'failed' && (
            <div
              className={cn(
                'flex items-center justify-between gap-3 border-b border-border px-4 py-2 text-label',
                TONE_TEXT[detail.tone === 'muted' ? 'neutral' : detail.tone],
              )}
            >
              <span>
                {detail.reason}
                {detail.note ? ` · ${detail.note}` : ''}
              </span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => retryMut.mutate(currentVideo)}
                disabled={retryMut.isPending}
              >
                <RotateCw aria-hidden="true" className="size-icon" />
                Retry
              </Button>
            </div>
          )}

          {/* Edited since publishing */}
          {showRepublish && (
            <div className="flex items-center justify-between gap-3 border-b border-st-review-fg/30 bg-st-review-bg px-4 py-2 text-label text-st-review-fg">
              <div className="flex items-center gap-2">
                <AlertCircle aria-hidden="true" className="size-icon shrink-0" />
                Edited since publishing
              </div>
              <Button
                size="sm"
                variant="primary"
                onClick={() => publishJob.publish(playlist?.stage_counts?.published ?? 0)}
                disabled={publishJob.isPublishing}
              >
                {publishJob.isPublishing ? (
                  <Loader aria-hidden="true" className="size-icon atlas-spin" />
                ) : (
                  <CloudUpload aria-hidden="true" className="size-icon" />
                )}
                {publishJob.isPublishing ? 'Publishing…' : 'Re-publish'}
              </Button>
            </div>
          )}

          {/* Preview / Edit switch and save bar */}
          <div className="flex items-center justify-between border-b border-border bg-surface-muted px-4 py-2">
            <div
              role="tablist"
              aria-label="Notes view"
              className="flex rounded border border-border bg-surface p-1"
            >
              {(['preview', 'edit'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  role="tab"
                  id={`notes-tab-${option}`}
                  aria-selected={mode === option}
                  aria-controls="notes-panel"
                  tabIndex={mode === option ? 0 : -1}
                  onClick={() => setMode(option)}
                  onKeyDown={(event) => {
                    // WAI-ARIA tabs: arrows move between Preview and Edit.
                    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
                    event.preventDefault()
                    const next = option === 'preview' ? 'edit' : 'preview'
                    setMode(next)
                    document.getElementById(`notes-tab-${next}`)?.focus()
                  }}
                  className={cn(
                    'rounded px-3 text-label transition',
                    mode === option
                      ? 'bg-surface-muted text-text'
                      : 'text-text-muted hover:text-text',
                  )}
                >
                  {option === 'preview' ? 'Preview' : 'Edit'}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-3">
              {isDirty && <span className="text-label text-st-review-fg">Unsaved changes</span>}
              <DisabledReason reason={!isDirty ? 'No unsaved changes' : null}>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={!isDirty || saveMut.isPending}
                  onClick={() => void save()}
                  title="Save changes (Ctrl+S)"
                >
                  <Save aria-hidden="true" className="size-icon" />
                  {saveMut.isPending ? 'Saving…' : 'Save'}
                </Button>
              </DisabledReason>
            </div>
          </div>

          {/* Body */}
          <div
            id="notes-panel"
            role="tabpanel"
            aria-labelledby={`notes-tab-${mode}`}
            className="flex-1 overflow-y-auto p-5"
          >
            {videos && !currentVideo ? (
              <EmptyState title="This video is not in the playlist.">
                <Link
                  to={`/playlists/${playlistId}`}
                  className="text-accent underline underline-offset-4"
                >
                  Back to playlist
                </Link>
              </EmptyState>
            ) : summaryQuery.isPending ? (
              <div
                className="mx-auto max-w-3xl space-y-3"
                aria-busy="true"
                aria-label="Loading notes"
              >
                <SkeletonBar className="h-4 w-1/2" />
                <SkeletonBar className="w-full" />
                <SkeletonBar className="w-11/12" />
                <SkeletonBar className="w-4/5" />
              </div>
            ) : notesMissing ? (
              <EmptyState title="Notes not generated yet.">
                {isRegenerating
                  ? 'New notes are being generated for this video.'
                  : currentVideo?.stage === 'failed'
                    ? 'Processing failed for this video. Use Retry above to try again.'
                    : 'They will appear here once this video has been processed.'}
              </EmptyState>
            ) : summaryQuery.isError ? (
              <ErrorState
                title="Could not load the notes"
                message={errorMessage(summaryQuery.error)}
                onRetry={() => void summaryQuery.refetch()}
              />
            ) : mode === 'preview' ? (
              <div className="mx-auto max-w-3xl">
                <MarkdownBody markdown={editedContent} />
              </div>
            ) : (
              <textarea
                value={editedContent}
                onChange={(event) =>
                  setDraft({ videoId: currentVideoPk, content: event.target.value })
                }
                aria-label="Edit notes (Markdown)"
                className="h-full min-h-96 w-full resize-none bg-transparent font-notes text-notes leading-notes text-text"
                placeholder="Markdown notes"
                spellCheck={false}
              />
            )}
          </div>
        </section>
      </div>

      <ConfirmDialog
        open={confirmRegen}
        onOpenChange={setConfirmRegen}
        title="Regenerate notes"
        description={`Regenerate the notes for ${positionLabel} "${currentVideo?.title ?? ''}" from the stored transcript? The current notes, including any edits you made, will be overwritten.`}
        confirmLabel="Regenerate notes"
        isDestructive
        isLoading={regenMut.isPending}
        onConfirm={() => regenMut.mutateAsync(currentVideoPk)}
      />

      {/* Navigating away with unsaved edits */}
      <ConfirmDialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => {
          if (open) return
          if (discardingRef.current) {
            discardingRef.current = false
            return
          }
          if (blocker.state === 'blocked') blocker.reset()
        }}
        title="Discard unsaved changes"
        description={`Discard unsaved changes to ${positionLabel || 'this video'}? Your edits will be lost.`}
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        isDestructive
        onConfirm={() => {
          discardingRef.current = true
          dirtyRef.current = false
          setDraft(null)
          if (blocker.state === 'blocked') blocker.proceed()
        }}
      />

      <ClearTranscriptDialog video={clearTarget} onClose={() => setClearTarget(null)} />
    </div>
  )
}
