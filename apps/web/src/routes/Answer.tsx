// Its own chunk: KaTeX and its CSS load only when an answer is shown.
import 'katex/dist/katex.min.css'
import type { ComponentProps } from 'react'
import Markdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

// Answers are model output that has read untrusted pages and photos, so they are untrusted too.
// Images stay out: a URL that loads by itself can carry a student's data to any server.
// Limits keep a huge or endlessly expanding formula from taking over the page.
const KATEX = { maxSize: 10, maxExpand: 100 }

/** The host a link goes to, shown beside it so link text cannot hide the destination. */
function host(href: string) {
  try {
    return new URL(href, window.location.href).host
  } catch {
    return ''
  }
}

/** A span. Display math scrolls in its own box, which keyboard users have to be able to reach. */
function Span(all: ComponentProps<'span'> & { node?: unknown }) {
  const { node: _, ...props } = all
  return props.className?.includes('katex-display') ? <span {...props} tabIndex={0} role="group" aria-label="Equation" /> : <span {...props} />
}

const COMPONENTS: Components = {
  img: ({ alt }) => <>{alt}</>,
  a: ({ href = '', children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer nofollow">
      {children} <span className="font-normal">({host(href)})</span>
    </a>
  ),
  span: Span,
}

/** Render answer Markdown with inline and display math through KaTeX. */
export default function Answer({ markdown }: { markdown: string }) {
  return (
    <div className="answer">
      <Markdown remarkPlugins={[remarkMath]} rehypePlugins={[[rehypeKatex, KATEX]]} components={COMPONENTS}>
        {markdown}
      </Markdown>
    </div>
  )
}
