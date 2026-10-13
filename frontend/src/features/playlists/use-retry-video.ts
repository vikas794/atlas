import { useMutation, useQueryClient } from '@tanstack/react-query'

import { retryVideo } from '../../lib/api'
import { failureMessage } from '../../lib/errors'
import { formatPosition } from '../../lib/format'
import { invalidateVideo } from '../../lib/query-keys'
import type { Video } from '../../lib/types'
import { useToast } from '../../components/shared/toast'

/** FR8: manual Retry of a failed video, shared by the videos table and the review screen. */
export function useRetryVideo(playlistId: number) {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  return useMutation({
    mutationFn: (video: Pick<Video, 'id' | 'position'>) => retryVideo(video.id),
    onSuccess: (_result, video) => {
      toast(`#${formatPosition(video.position)} queued for retry.`)
      void invalidateVideo(queryClient, playlistId, video.id)
    },
    onError: (err) => toast(failureMessage('Could not retry the video', err), 'error'),
  })
}
