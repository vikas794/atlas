import { AnimatePresence, motion } from 'framer-motion'
import { CheckCheck, Circle } from 'lucide-react'

import { MarkdownBody } from '../../../components/shared/markdown-body'
import { SectionHeading } from '../../../components/shared/section-heading'
import { Badge } from '../../../components/ui/badge'
import { Card } from '../../../components/ui/card'
import type { AssignmentArtifact } from '../../../lib/types'
import { cn } from '../../../lib/utils'
import { getAssignmentProgressItems } from '../pipeline-utils'
import { useAssignmentProgress } from '../use-assignment-progress'

interface AssignmentsTabProps {
  assignments: AssignmentArtifact[]
  activeRunId: string
}

export function AssignmentsTab({ assignments, activeRunId }: AssignmentsTabProps) {
  const { assignmentProgress, toggleAssignmentItem } = useAssignmentProgress(assignments, activeRunId)

  return (
    <AnimatePresence mode="popLayout">
      {assignments.map((item) => {
        const progressItems = getAssignmentProgressItems(item)
        const completedCount = progressItems.filter(
          (progressItem) => assignmentProgress[item.video_id]?.[progressItem.id],
        ).length
        const progressPercent =
          progressItems.length > 0 ? Math.round((completedCount / progressItems.length) * 100) : 0

        return (
          <motion.div
            key={item.video_id}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -18 }}
            initial={{ opacity: 0, y: 18 }}
          >
            <Card className="p-6">
              <div className="flex flex-wrap items-center gap-3">
                <h4 className="text-xl font-semibold tracking-[-0.03em] text-white">{item.title}</h4>
                <Badge>{item.channel}</Badge>
                <Badge>{item.available ? 'Ready' : 'Missing'}</Badge>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {Object.entries(item.display_metadata).map(([key, value]) => (
                  <Badge key={key}>
                    {key.replaceAll('_', ' ')}: {value}
                  </Badge>
                ))}
              </div>

              <div className="mt-6 grid gap-6 xl:grid-cols-[340px,minmax(0,1fr)]">
                <Card className="h-fit p-5">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">Progress</p>
                      <p className="mt-2 text-3xl font-semibold tracking-[-0.04em] text-white">
                        {progressPercent}%
                      </p>
                    </div>
                    <div className="rounded-2xl border border-white/8 bg-white/[0.04] p-3 text-white">
                      <CheckCheck className="size-5" />
                    </div>
                  </div>

                  <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className="h-full rounded-full bg-white transition-[width]"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>

                  <div className="mt-6 space-y-3">
                    {progressItems.map((progressItem) => {
                      const checked = Boolean(assignmentProgress[item.video_id]?.[progressItem.id])

                      return (
                        <button
                          key={progressItem.id}
                          className="flex w-full items-start gap-3 rounded-2xl border border-white/8 bg-black/10 px-3 py-3 text-left transition hover:bg-white/[0.03]"
                          onClick={() => toggleAssignmentItem(item.video_id, progressItem.id)}
                          type="button"
                        >
                          <span className="pt-0.5 text-white">
                            {checked ? <CheckCheck className="size-4" /> : <Circle className="size-4" />}
                          </span>
                          <span className={cn('text-sm leading-6 text-zinc-300', checked && 'text-white')}>
                            {progressItem.label}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </Card>

                <div className="space-y-4">
                  {item.sections.length > 0 ? (
                    item.sections.map((section) => (
                      <Card key={section.id} className="p-5">
                        <SectionHeading title={section.title} />
                        <MarkdownBody markdown={section.markdown} />
                      </Card>
                    ))
                  ) : (
                    <Card className="p-5">
                      <SectionHeading title="Assignment" />
                      <MarkdownBody markdown={item.markdown || 'No assignment content available.'} />
                    </Card>
                  )}
                </div>
              </div>
            </Card>
          </motion.div>
        )
      })}
    </AnimatePresence>
  )
}
