import { useEffect, useState } from 'react'

import type { AssignmentArtifact } from '../../lib/types'
import { getAssignmentStorageKey } from './pipeline-utils'

type ProgressMap = Record<string, boolean>

export function useAssignmentProgress(
  assignments: AssignmentArtifact[],
  activeRunId: string,
) {
  const [assignmentProgress, setAssignmentProgress] = useState<Record<string, ProgressMap>>({})

  useEffect(() => {
    const nextState: Record<string, ProgressMap> = {}
    if (activeRunId) {
      for (const item of assignments) {
        const saved = localStorage.getItem(getAssignmentStorageKey(activeRunId, item.video_id))
        if (!saved) {
          nextState[item.video_id] = {}
          continue
        }

        try {
          nextState[item.video_id] = JSON.parse(saved) as ProgressMap
        } catch {
          nextState[item.video_id] = {}
        }
      }
    }

    // Defer the state update so changing runs does not synchronously trigger a
    // second render while React is processing this synchronization effect.
    const timeoutId = window.setTimeout(() => setAssignmentProgress(nextState), 0)
    return () => window.clearTimeout(timeoutId)
  }, [activeRunId, assignments])

  const toggleAssignmentItem = (videoId: string, itemId: string) => {
    if (!activeRunId) return

    setAssignmentProgress((current) => {
      const nextVideoState = {
        ...(current[videoId] ?? {}),
        [itemId]: !(current[videoId] ?? {})[itemId],
      }
      localStorage.setItem(
        getAssignmentStorageKey(activeRunId, videoId),
        JSON.stringify(nextVideoState),
      )
      return {
        ...current,
        [videoId]: nextVideoState,
      }
    })
  }

  return { assignmentProgress, toggleAssignmentItem }
}
