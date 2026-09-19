import { ArrowUpRight } from 'lucide-react'

import { Badge } from '../../../components/ui/badge'
import { Card } from '../../../components/ui/card'
import type { VideoResult } from '../../../lib/types'
import { formatDate, getVideoThumbnail, trimText } from '../pipeline-utils'

interface VideosTabProps {
  videos: VideoResult[]
}

export function VideosTab({ videos }: VideosTabProps) {
  return (
    <div className="grid gap-5 xl:grid-cols-2">
      {videos.map((video) => (
        <Card key={video.video_id} className="overflow-hidden p-0">
          <div className="grid gap-0 md:grid-cols-[280px,minmax(0,1fr)]">
            <div className="relative aspect-video bg-black/30 md:aspect-auto">
              <img
                alt={video.title}
                className="h-full w-full object-cover"
                src={getVideoThumbnail(video.video_id)}
              />
              <div className="absolute bottom-3 right-3 rounded-lg bg-black/70 px-2 py-1 text-xs text-white">
                {video.duration}
              </div>
            </div>
            <div className="p-5">
              <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                <span>{video.channel}</span>
                <span className="text-zinc-700">/</span>
                <span>{formatDate(video.published_at)}</span>
              </div>
              <h4 className="mt-3 text-xl font-semibold tracking-[-0.03em] text-white">
                {video.title}
              </h4>
              <p className="mt-3 text-sm leading-7 text-zinc-300">
                {trimText(video.description || 'No description available for this result.', 240)}
              </p>
              <div className="mt-5 flex items-center justify-between gap-3">
                <div className="flex flex-wrap gap-2">
                  <Badge>{video.channel}</Badge>
                  <Badge>{video.duration}</Badge>
                </div>
                <a
                  className="inline-flex items-center gap-2 text-sm font-medium text-white"
                  href={video.url}
                  rel="noreferrer"
                  target="_blank"
                >
                  Watch
                  <ArrowUpRight className="size-4" />
                </a>
              </div>
            </div>
          </div>
        </Card>
      ))}
    </div>
  )
}
