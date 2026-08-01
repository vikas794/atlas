import { motion } from 'framer-motion'
import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { CheckCheck, Circle, ExternalLink, LoaderCircle, Youtube, AlertCircle } from 'lucide-react'

import { Badge } from '../../components/ui/badge'
import { Button } from '../../components/ui/button'
import { Card } from '../../components/ui/card'
import { generatePlaylistQuiz, getDriveStatus } from '../../lib/api'

interface QuizPanelProps {}

export function QuizPanel({}: QuizPanelProps) {
  const [playlistUrl, setPlaylistUrl] = useState('')
  const [maxVideos, setMaxVideos] = useState<number | ''>('')
  
  const driveStatusQuery = useQuery({
    queryKey: ['drive', 'status'],
    queryFn: getDriveStatus,
  })

  const quizMutation = useMutation({
    mutationFn: async () => {
      return generatePlaylistQuiz({
        playlist_url: playlistUrl,
        max_videos: maxVideos === '' ? undefined : maxVideos,
        use_env_keys: true,
      })
    },
  })

  const isRunning = quizMutation.isPending
  const result = quizMutation.data

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!playlistUrl.trim() || isRunning) return
    quizMutation.mutate()
  }

  return (
    <section id="quiz-generator" className="space-y-8">
      <motion.div
        animate={{ opacity: 1, y: 0 }}
        initial={{ opacity: 0, y: 20 }}
        transition={{ duration: 0.4 }}
      >
        <Card className="mx-auto max-w-6xl overflow-hidden border-white/8 bg-[linear-gradient(180deg,rgba(24,27,33,0.96),rgba(16,18,22,0.92))] p-6 md:p-8">
          <div className="mb-6">
            <h3 className="text-xl font-semibold tracking-[-0.03em] text-white">Playlist Quiz Generator</h3>
            <p className="mt-2 text-sm text-zinc-400">
              Convert a YouTube playlist into a series of active-recall quizzes saved to Google Drive.
            </p>
          </div>

          <form onSubmit={handleSubmit}>
            <div className="flex flex-col gap-4 xl:flex-row xl:items-end">
              <label className="block xl:flex-[2]">
                <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                  YouTube Playlist URL
                </span>
                <input
                  className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none placeholder:text-zinc-500 focus:border-white/15"
                  placeholder="https://youtube.com/playlist?list=..."
                  value={playlistUrl}
                  onChange={(e) => setPlaylistUrl(e.target.value)}
                  required
                />
              </label>

              <label className="block xl:w-48">
                <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                  Max Videos
                </span>
                <input
                  className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none focus:border-white/15 placeholder:text-zinc-500"
                  placeholder="All"
                  type="number"
                  min={1}
                  max={50}
                  value={maxVideos}
                  onChange={(e) => setMaxVideos(e.target.value ? Number(e.target.value) : '')}
                />
              </label>

              <Button
                className="h-12 gap-2 px-6 xl:min-w-[180px]"
                disabled={isRunning || !playlistUrl.trim()}
                type="submit"
              >
                {isRunning ? <LoaderCircle className="size-4 animate-spin" /> : <Youtube className="size-4" />}
                {isRunning ? 'Generating...' : 'Generate Quizzes'}
              </Button>
            </div>
            
            {driveStatusQuery.data && !driveStatusQuery.data.configured && (
               <div className="mt-4 flex items-center gap-2 rounded-xl border border-amber-500/20 bg-amber-500/10 p-3 text-sm text-amber-200">
                <AlertCircle className="size-4" />
                <span>Google Drive is not configured! Please ensure credentials.json exists and token.json is generated.</span>
              </div>
            )}
          </form>
        </Card>
      </motion.div>

      {quizMutation.isError && (
        <Card className="border-red-500/20 bg-red-500/10 p-5">
          <p className="text-sm text-red-200">
            {quizMutation.error instanceof Error ? quizMutation.error.message : 'Failed to generate quizzes'}
          </p>
        </Card>
      )}

      {result && (
        <motion.div
           animate={{ opacity: 1, y: 0 }}
           initial={{ opacity: 0, y: 20 }}
        >
          <Card className="p-6">
            <div className="mb-6 flex flex-wrap items-center justify-between gap-4 border-b border-white/8 pb-6">
              <div>
                <h4 className="text-xl font-semibold text-white">{result.playlist_title}</h4>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Badge>Total: {result.total_videos}</Badge>
                  <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                    Processed: {result.processed}
                  </Badge>
                  {result.failed > 0 && (
                     <Badge className="bg-red-500/10 text-red-400 border-red-500/20">
                       Failed: {result.failed}
                     </Badge>
                  )}
                </div>
              </div>
              
              {result.drive_folder_url && (
                <a
                  href={result.drive_folder_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-2 rounded-xl bg-white/[0.04] px-4 py-2.5 text-sm font-medium text-white transition hover:bg-white/[0.08]"
                >
                  <ExternalLink className="size-4" />
                  Open Drive Folder
                </a>
              )}
            </div>

            <div className="space-y-3">
              {result.video_results.map((video) => (
                <div 
                  key={video.video_id} 
                  className="flex flex-col gap-3 rounded-[20px] border border-white/8 bg-black/15 p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex items-start gap-3 sm:items-center">
                    <div className="mt-0.5 sm:mt-0">
                      {video.doc_url ? (
                        <CheckCheck className="size-4 text-emerald-400" />
                      ) : (
                        <Circle className="size-4 text-red-400" />
                      )}
                    </div>
                    <div>
                      <p className="text-sm font-medium text-zinc-200">
                        {video.position}. {video.title}
                      </p>
                      <p className="mt-1 text-xs text-zinc-500">{video.status}</p>
                    </div>
                  </div>
                  
                  {video.doc_url && (
                    <a
                      href={video.doc_url}
                      target="_blank"
                      rel="noreferrer"
                      className="shrink-0 rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-xs font-medium text-zinc-300 transition hover:bg-white/[0.06] hover:text-white"
                    >
                      View Doc
                    </a>
                  )}
                </div>
              ))}
            </div>
          </Card>
        </motion.div>
      )}
    </section>
  )
}
