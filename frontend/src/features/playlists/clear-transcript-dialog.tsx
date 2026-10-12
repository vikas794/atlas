import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deleteVideoTranscript } from '../../lib/api'
import { failureMessage } from '../../lib/errors'
import { formatPosition } from '../../lib/format'
import { invalidateVideo } from '../../lib/query-keys'
import type { Video } from '../../lib/types'
import { ConfirmDialog } from '../../components/shared/confirm-dialog'
import { useToast } from '../../components/shared/toast'

interface ClearTranscriptDialogProps {
  /** The video whose stored transcript would be deleted; null keeps the dialog closed. */
  video: Pick<Video, 'id' | 'playlist_id' | 'position' | 'title'> | null
  onClose: () => void
}

/**
 * FR11: delete one video's stored transcript, independent of its notes.
 * Shared by the videos table and the review screen so the wording is identical.
 */
export function ClearTranscriptDialog({ video, onClose }: ClearTranscriptDialogProps) {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const clearMutation = useMutation({
    mutationFn: (target: NonNullable<ClearTranscriptDialogProps['video']>) =>
      deleteVideoTranscript(target.id),
    onSuccess: (_result, target) => {
      toast(`Cleared the transcript for #${formatPosition(target.position)}.`)
      void invalidateVideo(queryClient, target.playlist_id, target.id)
    },
    onError: (err) => toast(failureMessage('Could not clear the transcript', err), 'error'),
  })

  return (
    <ConfirmDialog
      open={video !== null}
      onOpenChange={(open) => !open && onClose()}
      title="Clear transcript"
      description={
        video
          ? `Delete the stored transcript for #${formatPosition(video.position)} "${video.title}"? Its generated notes are kept, but regenerating them will need the transcript fetched again.`
          : ''
      }
      confirmLabel="Clear transcript"
      isDestructive
      isLoading={clearMutation.isPending}
      onConfirm={() => (video ? clearMutation.mutateAsync(video) : undefined)}
    />
  )
}
