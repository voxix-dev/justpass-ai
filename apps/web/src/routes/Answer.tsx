// Its own chunk: KaTeX and its CSS load only when an answer is shown.
import 'katex/dist/katex.min.css'
import Markdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'

export default function Answer({ markdown }: { markdown: string }) {
  return (
    <div className="answer">
      <Markdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]}>
        {markdown}
      </Markdown>
    </div>
  )
}
