/** Small presentational helpers shared by the usage dashboard and the key analytics view. */

export function Stat({
  title,
  value,
  description,
}: {
  title: string
  value: string
  description?: string
}) {
  return (
    <div className="panel card-lift p-5">
      <p className="text-label text-text-muted">{title}</p>
      <p className="mt-2 font-display text-[32px] font-semibold leading-9 tabular-nums text-text">
        {value}
      </p>
      {description ? <p className="mt-1 text-label text-text-muted">{description}</p> : null}
    </div>
  )
}

export function TableShell({ empty, children }: { empty?: string; children?: React.ReactNode }) {
  if (empty) {
    return <p className="panel p-4 text-center text-body text-text-muted">{empty}</p>
  }
  return (
    <div className="overflow-x-auto panel">
      <table className="min-w-full text-left text-body">{children}</table>
    </div>
  )
}

export function Head({ columns }: { columns: string[] }) {
  return (
    <thead className="bg-surface-muted text-label text-text">
      <tr className="h-row">
        {columns.map((heading) => (
          <th key={heading} scope="col" className="px-3">
            {heading}
          </th>
        ))}
      </tr>
    </thead>
  )
}
