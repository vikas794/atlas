import type { ReactNode } from 'react'

/** Page title block: large display title, one supporting sentence, actions on the right. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-5">
      <div className="max-w-3xl">
        <h1 className="text-page text-text">
          <span className="text-gradient">{title}</span>
        </h1>
        {description ? <p className="mt-2 text-body text-text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-3">{actions}</div> : null}
    </div>
  )
}
