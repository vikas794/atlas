import type {
  ArtifactGenerationRequest,
  PapersQueryResponse,
  PapersStatusResponse,
  PipelineActionResponse,
  RunBundle,
  RunManifest,
  SearchRequest,
  PlaylistQuizRequest,
  PlaylistQuizProgress,
  PlaylistQuizStatusResponse,
  DriveStatusResponse,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    let message = 'Request failed.'
    try {
      const errorBody = (await response.json()) as { detail?: string }
      message = errorBody.detail ?? message
    } catch {
      message = response.statusText || message
    }
    throw new Error(message)
  }

  return (await response.json()) as T
}

export function getRuns() {
  return request<{ runs: RunManifest[] }>('/api/runs')
}

export function getLatestRun() {
  return request<RunManifest>('/api/runs/latest')
}

export async function getRunBundle(runId: string): Promise<RunBundle> {
  const [videos, transcripts, summaries, comparison, assignments] = await Promise.all([
    request<RunBundle['videos']>(`/api/runs/${runId}/videos`),
    request<RunBundle['transcripts']>(`/api/runs/${runId}/transcripts`),
    request<RunBundle['summaries']>(`/api/runs/${runId}/summaries`),
    request<RunBundle['comparison']>(`/api/runs/${runId}/comparison`),
    request<RunBundle['assignments']>(`/api/runs/${runId}/assignments`),
  ])

  return {
    videos,
    transcripts,
    summaries,
    comparison,
    assignments,
  }
}

export function searchPipeline(payload: SearchRequest) {
  return request<PipelineActionResponse>('/api/pipeline/search', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function triggerTranscripts(runId: string, payload: ArtifactGenerationRequest) {
  return request<PipelineActionResponse>(`/api/runs/${runId}/transcripts`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function triggerSummaries(runId: string, payload: ArtifactGenerationRequest) {
  return request<PipelineActionResponse>(`/api/runs/${runId}/summaries`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function triggerComparison(runId: string, payload: ArtifactGenerationRequest) {
  return request<PipelineActionResponse>(`/api/runs/${runId}/comparison`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function triggerAssignments(runId: string, payload: ArtifactGenerationRequest) {
  return request<PipelineActionResponse>(`/api/runs/${runId}/assignments`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getPapersStatus() {
  return request<PapersStatusResponse>('/api/papers/status')
}

export function queryPapers(query: string) {
  return request<PapersQueryResponse>('/api/papers/query', {
    method: 'POST',
    body: JSON.stringify({ query }),
  })
}

export function generatePlaylistQuiz(payload: PlaylistQuizRequest) {
  return request<PlaylistQuizStatusResponse>('/api/quiz/playlist', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function generatePlaylistQuizStream(
  payload: PlaylistQuizRequest,
  onProgress: (progress: PlaylistQuizProgress) => void,
) {
  const response = await fetch(`${API_BASE_URL}/api/quiz/playlist/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!response.ok || !response.body) {
    throw new Error('Unable to start quiz generation.')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const events = buffer.split('\n\n')
    buffer = events.pop() ?? ''

    for (const event of events) {
      const data = event.split('\n').find((line) => line.startsWith('data: '))?.slice(6)
      if (!data) continue
      const message = JSON.parse(data) as PlaylistQuizProgress | ({ type: 'complete' } & PlaylistQuizStatusResponse) | { type: 'error'; message: string }
      if (message.type === 'progress') onProgress(message)
      if (message.type === 'complete') return message
      if (message.type === 'error') throw new Error(message.message)
    }

    if (done) break
  }

  throw new Error('Quiz generation ended before returning a result.')
}

export function getDriveStatus() {
  return request<DriveStatusResponse>('/api/quiz/drive-status')
}

export async function uploadCredentials(file: File) {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE_URL}/api/quiz/credentials`, {
    method: 'POST',
    body: formData,
    // note: intentionally omit 'Content-Type': 'application/json' 
  })

  if (!response.ok) {
    throw new Error('Failed to upload credentials')
  }

  return response.json() as Promise<{ status: string; message: string }>
}

export function authenticateDrive() {
  return request<{ status: string; message: string }>('/api/quiz/auth', {
    method: 'POST',
  })
}

