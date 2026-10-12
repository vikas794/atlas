import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getPlaylist, publishPlaylist } from '../../lib/api'
import { failureMessage } from '../../lib/errors'
import { describePublish, publishPhase, type JobWatch } from '../../lib/jobs'
import { FAST_POLL_MS } from '../../lib/polling'
import { invalidatePlaylist, queryKeys } from '../../lib/query-keys'
import { useToast } from '../../components/shared/toast'

/**
 * Publish to Drive. The backend answers 202 immediately (its counts are always
 * 0) and runs the publish in the worker, so the POST itself never counts as
 * success: the playlist goes to `status: 'publishing'` and is polled until it
 * settles, then the real outcome is toasted (`publish_failed` shows the stored
 * error, otherwise how many videos were published).
 */
export function usePublishJob(playlistId: number) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [watch, setWatch] = useState<JobWatch | null>(null)

  // Same key as the screen's own playlist query: TanStack shares the cache and
  // polls at the shortest interval, so this only speeds polling up while watching.
  const playlistQuery = useQuery({
    queryKey: queryKeys.playlist(playlistId),
    queryFn: () => getPlaylist(playlistId),
    enabled: watch !== null,
    refetchInterval: watch ? FAST_POLL_MS : false,
  })
  const playlist = playlistQuery.data
  const phase = publishPhase(playlist, playlistQuery.dataUpdatedAt, watch)

  const latest = useRef({ playlist, watch })
  useEffect(() => {
    latest.current = { playlist, watch }
  })

  useEffect(() => {
    if (phase !== 'succeeded' && phase !== 'failed') return
    const { playlist: current, watch: started } = latest.current
    if (phase === 'failed') {
      toast(
        `Publishing to Drive failed: ${current?.last_publish_error || 'no reason was recorded.'}`,
        'error',
      )
    } else {
      const delta = Math.max(
        0,
        (current?.stage_counts?.published ?? 0) - (started?.publishedBefore ?? 0),
      )
      toast(describePublish(delta))
    }
    // The job is over: stop watching and refresh everything it touched.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWatch(null)
    void invalidatePlaylist(queryClient, playlistId)
    void queryClient.invalidateQueries({ queryKey: ['video-summary'] })
  }, [phase, toast, queryClient, playlistId])

  const mutation = useMutation({
    mutationFn: async (publishedBefore: number) => {
      await publishPlaylist(playlistId)
      return publishedBefore
    },
    onSuccess: (publishedBefore) => {
      setWatch({ startedAt: Date.now(), publishedBefore })
      void invalidatePlaylist(queryClient, playlistId)
    },
    onError: (err) => toast(failureMessage('Could not start publishing to Drive', err), 'error'),
  })

  return {
    /** Start publishing; `publishedBefore` is the current published count (for the delta). */
    publish: (publishedBefore: number) => mutation.mutate(publishedBefore),
    isPublishing: mutation.isPending || phase === 'running',
  }
}
