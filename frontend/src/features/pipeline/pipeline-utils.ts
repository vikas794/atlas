import type { AssignmentArtifact } from '../../lib/types'

export function formatDate(value: string) {
  if (!value) return 'Unavailable'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function getVideoThumbnail(videoId: string) {
  return `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`
}

export function trimText(value: string, maxLength: number) {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength - 1).trimEnd()}...`
}

export function splitBreakdown(entries: Array<Record<string, unknown>>) {
  const tools = entries.filter((entry) => entry.type === 'tool')
  const processes = entries.filter((entry) => entry.type === 'process')
  const architecture = entries.filter((entry) => entry.type !== 'tool' && entry.type !== 'process')
  return { tools, processes, architecture }
}

export function getAssignmentStorageKey(runId: string, videoId: string) {
  return `atlas-assignment-progress:${runId}:${videoId}`
}

export function getAssignmentProgressItems(item: AssignmentArtifact) {
  if (item.checklist.length > 0) {
    return item.checklist
  }

  if (item.sections.length > 0) {
    return item.sections.map((section) => ({
      id: section.id,
      label: section.title,
    }))
  }

  return item.markdown
    ? [
        {
          id: 'review-assignment',
          label: 'Review assignment',
        },
      ]
    : []
}
