import { SectionHeading } from '../../../components/shared/section-heading'
import { Badge } from '../../../components/ui/badge'
import { Card } from '../../../components/ui/card'
import type { ComparisonRow } from '../../../lib/types'

interface ComparisonTabProps {
  comparison: ComparisonRow[]
  recommendations: string[]
  insightsReport: string
}

export function ComparisonTab({ comparison, recommendations, insightsReport }: ComparisonTabProps) {
  return (
    <>
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-white/8 text-left text-sm">
            <thead className="bg-black/10 text-zinc-500">
              <tr>
                {[
                  'Title',
                  'Difficulty',
                  'Teaching style',
                  'Depth',
                  'Practical value',
                  'Audience',
                  'Technologies',
                ].map((heading) => (
                  <th key={heading} className="px-4 py-4 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/6">
              {comparison.map((row) => (
                <tr key={row.video_id} className="align-top">
                  <td className="px-4 py-4">
                    <p className="font-medium text-white">{row.title}</p>
                    <p className="mt-2 text-xs text-zinc-500">{row.channel}</p>
                  </td>
                  <td className="px-4 py-4 text-zinc-200">{row.difficulty}</td>
                  <td className="px-4 py-4 text-zinc-200">{row.teaching_style}</td>
                  <td className="px-4 py-4 text-zinc-200">{row.content_depth}</td>
                  <td className="px-4 py-4 text-zinc-200">{row.practical_value}</td>
                  <td className="px-4 py-4 text-zinc-200">{row.target_audience}</td>
                  <td className="px-4 py-4 text-zinc-200">
                    <div className="flex flex-wrap gap-2">
                      {row.key_technologies.map((technology) => (
                        <Badge key={technology}>{technology}</Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[0.72fr,1.28fr]">
        <Card className="p-5">
          <SectionHeading title="Recommendations" />
          <ul className="space-y-3 text-sm leading-7 text-zinc-300">
            {recommendations.map((recommendation) => (
              <li key={recommendation}>• {recommendation}</li>
            ))}
          </ul>
        </Card>
        <Card className="p-5">
          <SectionHeading title="Insights report" />
          <pre className="whitespace-pre-wrap text-sm leading-7 text-zinc-300">{insightsReport}</pre>
        </Card>
      </div>
    </>
  )
}
