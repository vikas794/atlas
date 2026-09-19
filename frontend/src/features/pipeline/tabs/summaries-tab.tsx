import { SectionHeading } from '../../../components/shared/section-heading'
import { Badge } from '../../../components/ui/badge'
import { Card } from '../../../components/ui/card'
import type { SummaryArtifact } from '../../../lib/types'
import { splitBreakdown } from '../pipeline-utils'

interface SummariesTabProps {
  summaries: SummaryArtifact[]
}

export function SummariesTab({ summaries }: SummariesTabProps) {
  return (
    <>
      {summaries.map((item) => {
        const breakdown = splitBreakdown(item.technical_breakdown)

        return (
          <Card key={item.video_id} className="overflow-hidden p-0">
            <div className="border-b border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.01))] px-6 py-5">
              <div className="flex flex-wrap items-center gap-3">
                <h4 className="text-xl font-semibold tracking-[-0.03em] text-white">{item.title}</h4>
                <Badge>{item.channel}</Badge>
              </div>
            </div>

            <div className="space-y-6 p-6">
              <div className="grid gap-6 xl:grid-cols-[1.2fr,0.8fr]">
                <div className="rounded-[28px] border border-white/8 bg-[linear-gradient(145deg,rgba(255,255,255,0.05),rgba(255,255,255,0.015))] p-6">
                  <p className="text-[11px] uppercase tracking-[0.24em] text-zinc-500">Overview</p>
                  <p className="mt-5 max-w-3xl text-[15px] leading-8 text-zinc-200">
                    {item.high_level_overview}
                  </p>
                </div>

                <div className="rounded-[28px] border border-white/8 bg-black/15 p-6">
                  <SectionHeading
                    title="Architecture"
                    description="System structure and orchestration patterns."
                  />
                  {breakdown.architecture.length > 0 ? (
                    <div className="space-y-4">
                      {breakdown.architecture.map((entry, index) => (
                        <div
                          key={`${item.video_id}-architecture-${index}`}
                          className="rounded-[24px] border border-white/8 bg-white/[0.03] p-5"
                        >
                          <p className="text-sm leading-7 text-zinc-300">
                            {String(entry.description ?? 'Architecture detail')}
                          </p>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-zinc-400">No explicit architecture notes were extracted.</p>
                  )}
                </div>
              </div>

              <div className="grid gap-6 xl:grid-cols-[0.95fr,1.05fr]">
                <div className="rounded-[28px] border border-white/8 bg-black/15 p-6">
                  <SectionHeading
                    title="Process flow"
                    description="The main implementation sequence broken into steps."
                  />
                  <div className="space-y-4">
                    {breakdown.processes.map((entry, index) => (
                      <div
                        key={`${item.video_id}-process-${index}`}
                        className="relative overflow-hidden rounded-[24px] border border-white/8 bg-white/[0.03] p-5"
                      >
                        <div className="absolute inset-y-0 left-0 w-px bg-white/10" />
                        <div className="pl-4">
                          <p className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">
                            Step {String(entry.step_number ?? index + 1)}
                          </p>
                          <p className="mt-3 text-sm leading-7 text-zinc-300">
                            {String(entry.description ?? '')}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-[28px] border border-white/8 bg-black/15 p-6">
                  <SectionHeading
                    title="Tools"
                    description="Frameworks, models, and systems referenced in the walkthrough."
                  />
                  <div className="grid gap-4 md:grid-cols-2">
                    {breakdown.tools.map((entry, index) => (
                      <div
                        key={`${item.video_id}-tool-${index}`}
                        className="rounded-[24px] border border-white/8 bg-white/[0.03] p-5"
                      >
                        <p className="text-sm font-medium text-white">
                          {String(entry.name ?? 'Tool')}
                        </p>
                        <p className="mt-3 text-sm leading-7 text-zinc-300">
                          {String(entry.purpose ?? '')}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="grid gap-6 xl:grid-cols-3">
                <div className="rounded-[28px] border border-emerald-400/12 bg-emerald-400/[0.04] p-6">
                  <SectionHeading title="Insights" description="Key takeaways and notable design decisions." />
                  <ul className="space-y-3">
                    {item.insights.map((insight, index) => (
                      <li
                        key={insight}
                        className="rounded-[20px] border border-white/8 bg-black/10 px-4 py-4 text-sm leading-7 text-zinc-300"
                      >
                        <span className="mr-3 text-zinc-500">{String(index + 1).padStart(2, '0')}</span>
                        {insight}
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="rounded-[28px] border border-sky-400/12 bg-sky-400/[0.04] p-6">
                  <SectionHeading title="Applications" description="How the concepts translate into practical use." />
                  <ul className="space-y-3">
                    {item.applications.map((application, index) => (
                      <li
                        key={application}
                        className="rounded-[20px] border border-white/8 bg-black/10 px-4 py-4 text-sm leading-7 text-zinc-300"
                      >
                        <span className="mr-3 text-zinc-500">{String(index + 1).padStart(2, '0')}</span>
                        {application}
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="rounded-[28px] border border-amber-400/12 bg-amber-400/[0.04] p-6">
                  <SectionHeading title="Limitations" description="Trade-offs, caveats, and implementation constraints." />
                  <ul className="space-y-3">
                    {item.limitations.map((limitation, index) => (
                      <li
                        key={limitation}
                        className="rounded-[20px] border border-white/8 bg-black/10 px-4 py-4 text-sm leading-7 text-zinc-300"
                      >
                        <span className="mr-3 text-zinc-500">{String(index + 1).padStart(2, '0')}</span>
                        {limitation}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </Card>
        )
      })}
    </>
  )
}
