import { Badge } from '../../../components/ui/badge'
import { Card } from '../../../components/ui/card'
import type { TranscriptArtifact } from '../../../lib/types'

interface TranscriptsTabProps {
  transcripts: TranscriptArtifact[]
}

export function TranscriptsTab({ transcripts }: TranscriptsTabProps) {
  return (
    <>
      {transcripts.map((item) => (
        <Card key={item.video_id} className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h4 className="text-lg font-semibold text-white">{item.title}</h4>
              <p className="mt-1 text-sm text-zinc-400">{item.channel}</p>
            </div>
            <Badge>{item.available ? item.language : 'Missing'}</Badge>
          </div>
          <p className="mt-4 max-h-[420px] overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-zinc-300">
            {item.cleaned_text || 'No transcript available for this video yet.'}
          </p>
        </Card>
      ))}
    </>
  )
}
