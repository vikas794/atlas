import { motion } from 'framer-motion'
import { LoaderCircle, Workflow } from 'lucide-react'
import { type ChangeEvent, type FormEvent } from 'react'

import { Button } from '../../components/ui/button'
import { Card } from '../../components/ui/card'

interface RunFormProps {
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

export function RunForm({
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
}: RunFormProps) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    onStartRun()
  }

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      initial={{ opacity: 0, y: 20 }}
      transition={{ duration: 0.4 }}
    >
      <Card className="mx-auto max-w-6xl overflow-hidden border-white/8 bg-[linear-gradient(180deg,rgba(24,27,33,0.96),rgba(16,18,22,0.92))] p-6 md:p-8">
        <form onSubmit={handleSubmit}>
          <div className="flex flex-col gap-3 xl:flex-row xl:items-end">
            <label className="block xl:flex-[1.8]">
              <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                Search query
              </span>
              <input
                className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none placeholder:text-zinc-500 focus:border-white/15"
                placeholder="CrewAI tutorial"
                value={searchQuery}
                onChange={(event: ChangeEvent<HTMLInputElement>) =>
                  onSearchQueryChange(event.target.value)
                }
              />
            </label>

            <label className="block xl:w-36">
              <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                Max videos
              </span>
              <input
                className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none focus:border-white/15"
                max={10}
                min={1}
                type="number"
                value={maxVideos}
                onChange={(event) => onMaxVideosChange(Number(event.target.value))}
              />
            </label>

            <label className="block xl:w-44">
              <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                Transcript language
              </span>
              <select
                className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none focus:border-white/15"
                value={transcriptLanguage}
                onChange={(event) => onTranscriptLanguageChange(event.target.value)}
              >
                {['en', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'ko', 'zh'].map((language) => (
                  <option key={language} className="bg-[#13161a]" value={language}>
                    {language}
                  </option>
                ))}
              </select>
            </label>

            <label className="block xl:w-32">
              <span className="mb-2 block text-xs uppercase tracking-[0.2em] text-zinc-500">
                Workers
              </span>
              <input
                className="h-12 w-full rounded-2xl border border-white/8 bg-white/[0.04] px-4 text-sm text-white outline-none focus:border-white/15"
                max={16}
                min={0}
                type="number"
                value={numWorkers}
                onChange={(event) => onNumWorkersChange(Number(event.target.value))}
              />
            </label>

            <Button
              className="h-12 gap-2 px-5 xl:min-w-[170px]"
              disabled={isRunning || !searchQuery.trim()}
              type="submit"
            >
              {isRunning ? <LoaderCircle className="size-4 animate-spin" /> : <Workflow className="size-4" />}
              {isRunning ? 'Running...' : 'Run'}
            </Button>
          </div>
        </form>
      </Card>
    </motion.div>
  )
}
