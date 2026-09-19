import * as Tabs from '@radix-ui/react-tabs'
import { Layers3 } from 'lucide-react'
import { useMemo } from 'react'

import { SectionHeading } from '../../components/shared/section-heading'
import { Card } from '../../components/ui/card'
import type { RunBundle } from '../../lib/types'
import { RunForm } from './run-form'
import { AssignmentsTab } from './tabs/assignments-tab'
import { ComparisonTab } from './tabs/comparison-tab'
import { SummariesTab } from './tabs/summaries-tab'
import { TranscriptsTab } from './tabs/transcripts-tab'
import { VideosTab } from './tabs/videos-tab'

interface PipelineDashboardProps {
  activeRunId: string
  bundle?: RunBundle
  isLoading: boolean
  error?: string
  searchQuery: string
  maxVideos: number
  transcriptLanguage: string
  numWorkers: number
  onSearchQueryChange: (value: string) => void
  onMaxVideosChange: (value: number) => void
  onTranscriptLanguageChange: (value: string) => void
  onNumWorkersChange: (value: number) => void
  onStartRun: () => void
  isRunning: boolean
}

export function PipelineDashboard({
  activeRunId,
  bundle,
  isLoading,
  error,
  searchQuery,
  maxVideos,
  transcriptLanguage,
  numWorkers,
  onSearchQueryChange,
  onMaxVideosChange,
  onTranscriptLanguageChange,
  onNumWorkersChange,
  onStartRun,
  isRunning,
}: PipelineDashboardProps) {
  const videos = bundle?.videos.videos ?? []
  const transcripts = bundle?.transcripts.items ?? []
  const summaries = bundle?.summaries.items ?? []
  const comparison = bundle?.comparison.rows ?? []
  const assignments = useMemo(() => bundle?.assignments.items ?? [], [bundle?.assignments.items])

  return (
    <section id="pipeline" className="space-y-8">
      <RunForm
        isRunning={isRunning}
        maxVideos={maxVideos}
        numWorkers={numWorkers}
        onMaxVideosChange={onMaxVideosChange}
        onNumWorkersChange={onNumWorkersChange}
        onSearchQueryChange={onSearchQueryChange}
        onStartRun={onStartRun}
        onTranscriptLanguageChange={onTranscriptLanguageChange}
        searchQuery={searchQuery}
        transcriptLanguage={transcriptLanguage}
      />

      <Card className="p-6">
        <SectionHeading
          title="Analysis workspace"
          description="Structured video search, transcripts, summaries, comparison, and assignments."
        />

        {error ? (
          <p className="rounded-2xl border border-red-400/20 bg-red-500/8 p-4 text-sm text-red-100">
            {error}
          </p>
        ) : null}

        {isLoading ? (
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <div
                key={index}
                className="h-28 animate-pulse rounded-[24px] border border-white/8 bg-white/[0.04]"
              />
            ))}
          </div>
        ) : null}

        {!isLoading && bundle ? (
          <Tabs.Root className="space-y-6" defaultValue="videos">
            <Tabs.List className="flex flex-wrap gap-2 rounded-3xl border border-white/8 bg-black/10 p-2">
              {[
                ['videos', 'Search results'],
                ['transcripts', 'Transcripts'],
                ['summaries', 'Summaries'],
                ['comparison', 'Comparison'],
                ['assignments', 'Assignments'],
              ].map(([value, label]) => (
                <Tabs.Trigger
                  key={value}
                  className="rounded-2xl px-4 py-2 text-sm font-medium text-zinc-400 outline-none transition data-[state=active]:bg-white data-[state=active]:text-[#101217]"
                  value={value}
                >
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>

            <Tabs.Content className="space-y-4" value="videos">
              <VideosTab videos={videos} />
            </Tabs.Content>

            <Tabs.Content className="space-y-4" value="transcripts">
              <TranscriptsTab transcripts={transcripts} />
            </Tabs.Content>

            <Tabs.Content className="space-y-5" value="summaries">
              <SummariesTab summaries={summaries} />
            </Tabs.Content>

            <Tabs.Content className="space-y-4" value="comparison">
              <ComparisonTab
                comparison={comparison}
                insightsReport={bundle.comparison.insights_report}
                recommendations={bundle.comparison.recommendations}
              />
            </Tabs.Content>

            <Tabs.Content className="space-y-5" value="assignments">
              <AssignmentsTab activeRunId={activeRunId} assignments={assignments} />
            </Tabs.Content>
          </Tabs.Root>
        ) : null}

        {!isLoading && !bundle ? (
          <Card className="border-dashed border-white/10 p-10 text-center">
            <div className="mx-auto flex max-w-md flex-col items-center">
              <div className="rounded-2xl border border-white/8 bg-white/[0.04] p-3 text-white">
                <Layers3 className="size-5" />
              </div>
              <h3 className="mt-4 text-lg font-semibold text-white">No workspace loaded</h3>
              <p className="mt-2 text-sm leading-7 text-zinc-400">
                Start a run to populate the workspace with YouTube search results, summaries, comparison,
                and assignments.
              </p>
            </div>
          </Card>
        ) : null}
      </Card>
    </section>
  )
}
