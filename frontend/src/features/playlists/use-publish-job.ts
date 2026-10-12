import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getPlaylist, publishPlaylist } from '../../lib/api'
import { describePublish, hasPublishCounts, publishPhase, type JobWatch } from '../../lib/jobs'
import { FAST_POLL_MS } from '../../lib/polling'
import { invalidatePlaylist, queryKeys } from '../../lib/query-keys'
import { useToast } from '../../components/shared/toast'

/**
 * Publish to Drive, for both backend flavours:
 * - sync (200): the response carries counts and the toast reports them;
 * - async (202): the playlist goes to `status: 'publishing'`; this hook polls
 *   the playlist until it settles, then toasts the real outcome
 *   (`publish_failed` shows the stored error, otherwise the counts or
 *   "Nothing new to publish").
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
      toast(current?.last_publish_error || 'Publishing to Drive failed.', 'error')
    } else {
      const delta = Math.max(
        0,
        (current?.stage_counts?.published ?? 0) - (started?.publishedBefore ?? 0),
      )
      toast(describePublish(current?.last_publish ?? null, delta))
    }
    // The async job is over: stop watching and refresh everything it touched.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWatch(null)
    void invalidatePlaylist(queryClient, playlistId)
    void queryClient.invalidateQueries({ queryKey: ['video-summary'] })
  }, [phase, toast, queryClient, playlistId])

  const mutation = useMutation({
    mutationFn: async (publishedBefore: number) => ({
      response: await publishPlaylist(playlistId),
      publishedBefore,
    }),
    onSuccess: ({ response, publishedBefore }) => {
      if (hasPublishCounts(response)) {
        toast(describePublish(response))
        void queryClient.invalidateQueries({ queryKey: ['video-summary'] })
      } else {
        setWatch({ startedAt: Date.now(), publishedBefore })
      }
      void invalidatePlaylist(queryClient, playlistId)
    },
    onError: (err: Error) => toast(err.message || 'Publishing to Drive failed.', 'error'),
  })

  return {
    /** Start publishing; `publishedBefore` is the current published count (for the async delta). */
    publish: (publishedBefore: number) => mutation.mutate(publishedBefore),
    isPublishing: mutation.isPending || phase === 'running',
  }
}
