import { motion } from 'framer-motion'
import { useState } from 'react'

import { MarkdownBody } from '../../components/shared/markdown-body'
import { Badge } from '../../components/ui/badge'
import { Card } from '../../components/ui/card'
import { cn } from '../../lib/utils'
import { demoPlaylistNotes } from './notes-demo-data'

const MASTER_TAB = 'master'

export function NotesDemoPanel() {
  const [activeTab, setActiveTab] = useState<string>(MASTER_TAB)
  const { playlistTitle, channel, videoCount, totalDuration, masterGuide, videos } = demoPlaylistNotes

  const activeVideo = videos.find((video) => String(video.position) === activeTab)
  const markdown = activeVideo ? activeVideo.markdown : masterGuide

  return (
    <section id="playlist-notes" className="space-y-8">
      <motion.div animate={{ opacity: 1, y: 0 }} initial={{ opacity: 0, y: 20 }} transition={{ duration: 0.4 }}>
        <Card className="mx-auto max-w-6xl overflow-hidden border-white/8 bg-[linear-gradient(180deg,rgba(24,27,33,0.96),rgba(16,18,22,0.92))] p-6 md:p-8">
          <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h3 className="text-xl font-semibold tracking-[-0.03em] text-white">Playlist Notes</h3>
                <Badge className="border-amber-400/20 bg-amber-400/10 text-amber-200">Demo preview</Badge>
              </div>
              <p className="mt-2 text-sm text-zinc-400">
                Sample layout with placeholder content. Real notes will be generated from a playlist's transcripts.
              </p>
            </div>
          </div>

          <div className="mb-6 rounded-2xl border border-white/8 bg-white/[0.03] p-4">
            <p className="text-base font-semibold text-white">{playlistTitle}</p>
            <p className="mt-1 text-sm text-zinc-400">
              {channel} · {videoCount} videos · {totalDuration}
            </p>
          </div>

          <div className="mb-6 flex flex-wrap gap-2" role="tablist">
            <TabButton active={activeTab === MASTER_TAB} onClick={() => setActiveTab(MASTER_TAB)}>
              Master guide
            </TabButton>
            {videos.map((video) => (
              <TabButton
                active={activeTab === String(video.position)}
                key={video.position}
                onClick={() => setActiveTab(String(video.position))}
              >
                {video.position}. {video.title}
              </TabButton>
            ))}
          </div>

          <div className="rounded-2xl border border-white/8 bg-black/20 p-5 md:p-7">
            {activeVideo ? (
              <div className="mb-4">
                <h4 className="text-lg font-semibold tracking-[-0.03em] text-white">
                  {activeVideo.position}. {activeVideo.title}
                </h4>
                <p className="mt-1 text-xs uppercase tracking-[0.2em] text-zinc-500">{activeVideo.duration}</p>
              </div>
            ) : null}
            <MarkdownBody markdown={markdown} />
          </div>
        </Card>
      </motion.div>
    </section>
  )
}

function TabButton({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      aria-selected={active}
      className={cn(
        'rounded-full border px-4 py-2 text-sm transition-colors',
        active
          ? 'border-white/20 bg-white/10 text-white'
          : 'border-white/8 bg-white/[0.03] text-zinc-400 hover:text-white',
      )}
      onClick={onClick}
      role="tab"
      type="button"
    >
      {children}
    </button>
  )
}
