import type { ReactNode } from 'react'

export function SectionHeading({
  title,
  description,
  action,
}: {
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <h3 className="text-lg font-semibold tracking-[-0.03em] text-white">{title}</h3>
        {description ? <p className="mt-1 text-sm text-zinc-400">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}
