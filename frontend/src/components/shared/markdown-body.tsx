import { Children, isValidElement, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cn } from '../../lib/utils'

/** Pull the fence tag (`language-python`) out of the `<code>` inside `<pre>`. */
function fenceLanguage(children: ReactNode): string {
  const [child] = Children.toArray(children)
  if (!isValidElement<{ className?: string }>(child)) return ''
  const match = /language-([\w+#-]+)/.exec(child.props.className ?? '')
  return match ? match[1] : ''
}

/**
 * Rendered notes for the Review screen.
 *
 * Section 4: notes content is Lexend at 15px / 26px. Section 7: code blocks
 * are JetBrains Mono 13px on `--surface-muted` with a 1px border, 6px radius,
 * horizontal scroll and a language label in the top-right corner.
 */
export function MarkdownBody({ markdown, className }: { markdown: string; className?: string }) {
  return (
    <div
      className={cn(
        'space-y-4 font-notes text-notes text-text',
        '[&_h1]:text-section [&_h1]:font-semibold [&_h1]:text-text',
        '[&_h2]:text-section [&_h2]:font-semibold [&_h2]:text-text',
        '[&_h3]:text-body [&_h3]:font-semibold [&_h3]:text-text',
        '[&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-5',
        '[&_ol]:list-decimal [&_ol]:space-y-2 [&_ol]:pl-5',
        '[&_table]:w-full [&_table]:text-body [&_table]:font-ui [&_th]:border-b [&_th]:border-border [&_th]:bg-surface-muted [&_th]:text-left [&_th]:font-medium [&_th]:text-text [&_td]:border-b [&_td]:border-border [&_td]:align-top',
        '[&_th]:px-3 [&_th]:py-1 [&_td]:px-3 [&_td]:py-1',
        '[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:text-text-muted',
        '[&_hr]:border-border',
        className,
      )}
    >
      <ReactMarkdown
        components={{
          // Notes are AI output: react-markdown never renders raw HTML (no rehype-raw) and its
          // default urlTransform drops javascript:/data: URLs. rel/target come last so the
          // Markdown cannot override them.
          a: ({ className: anchorClassName, node, ...props }) => (
            <a
              {...props}
              className={cn('text-accent underline underline-offset-4', anchorClassName)}
              rel="noopener noreferrer"
              target="_blank"
            />
          ),
          pre: ({ children, className: preClassName, node, ...props }) => {
            const language = fenceLanguage(children)
            return (
              <div className="relative">
                {language ? (
                  <span className="absolute right-2 top-1 select-none text-label text-text">
                    {language}
                  </span>
                ) : null}
                <pre
                  className={cn(
                    'overflow-x-auto rounded border border-border bg-surface-muted p-3 pt-6',
                    'font-mono text-code text-text',
                    preClassName,
                  )}
                  {...props}
                >
                  {children}
                </pre>
              </div>
            )
          },
          code: ({ className: codeClassName, node, ...props }) => {
            const isFenced = /language-/.test(codeClassName ?? '')
            return (
              <code
                className={cn(
                  'font-mono text-code text-text',
                  isFenced ? null : 'rounded bg-surface-muted px-1',
                  codeClassName,
                )}
                {...props}
              />
            )
          },
        }}
        remarkPlugins={[remarkGfm]}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
