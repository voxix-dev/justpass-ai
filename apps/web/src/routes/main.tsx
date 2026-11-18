import { Link } from 'react-router'
import { useAuth } from '@/auth/auth'
import { Button } from '@/components/ui/button'

/** Landing page content. The application shell owns the site header and footer. */
export default function Landing() {
  const isAuthenticated = Boolean(useAuth())
  const primaryLabel = isAuthenticated ? 'Send a task' : 'Sign up'

  return (
    <div className="space-y-24 pb-12 sm:space-y-32">
      {/* Hero */}
      <section className="wrap grid items-center gap-12 py-10 sm:py-16 lg:grid-cols-[1.05fr_0.95fr] lg:gap-16">
        <div className="max-w-2xl">
          <p className="pill mb-6 bg-primary-soft text-primary-ink">Homework, handled</p>
          <h1 className="text-display max-w-[11ch]">
            Send the homework. <mark>Forget it.</mark>
          </h1>
          <p className="mt-6 max-w-[34rem] text-lg leading-relaxed text-muted-foreground sm:text-xl">
            JustPass solves it while you’re doing something else. You get a file, copy it onto paper and hand it in.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild size="lg" className="h-12 px-6">
              <Link to={isAuthenticated ? '/new' : '/login'}>{primaryLabel}</Link>
            </Button>
            <Button asChild size="lg" variant="outline" className="h-12 px-6">
              <Link to="/new">Try as guest</Link>
            </Button>
          </div>
        </div>

        <div className="relative mx-auto w-full max-w-xl">
          <div className="card overflow-hidden p-5 shadow-float sm:p-7">
            <p className="text-sm leading-relaxed text-muted-foreground sm:text-base">
              Solved all 12 equations, line by line, ready to copy onto paper.
            </p>
            <div className="mt-5 overflow-hidden rounded-xl border border-border bg-surface">
              <div className="flex flex-wrap items-center gap-3 p-4">
                <div className="flex h-12 w-10 shrink-0 items-center justify-center rounded-md bg-sand text-xs font-bold text-foreground">
                  PDF
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">quadratics-solutions.pdf</p>
                  <p className="text-xs text-muted-foreground">PDF · 3 pages · 12 equations</p>
                </div>
                <span className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground">Download</span>
              </div>
              <div className="relative h-40 overflow-hidden px-4">
                <div className="space-y-2 rounded-t-md border border-border bg-white px-5 py-4 font-math text-base text-foreground sm:text-lg">
                  <p><strong>1.</strong> 2<i>x</i><sup>2</sup> − <i>x</i> + 69 = 0</p>
                  <p className="pl-6">Δ = (−1)<sup>2</sup> − 4 · 2 · 69 = −551</p>
                  <p className="pl-6"><i>x</i> = (1 ± i√551) / 4</p>
                  <p><strong>2.</strong> 3<i>x</i><sup>2</sup> + 2<i>x</i> − 8 = 0</p>
                  <p className="pl-6">Δ = 2<sup>2</sup> − 4 · 3 · (−8) = 100</p>
                </div>
                <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-b from-transparent to-surface" />
              </div>
            </div>
          </div>
          <span className="absolute -left-3 -top-4 rotate-[-6deg] rounded-lg bg-highlight px-3 py-2 text-sm font-bold shadow-sm sm:left-4">Ready to copy</span>
          <span className="absolute -bottom-4 -right-2 rotate-[4deg] rounded-lg bg-ink px-3 py-2 text-sm font-bold text-background shadow-sm sm:right-5">You’re done</span>
        </div>
      </section>

      {/* Product explanation */}
      <section className="wrap">
        <div className="mb-10 grid gap-5 md:grid-cols-2 md:items-end md:gap-10">
          <h2 className="text-headline max-w-[12ch]">Not a chatbot.<br />A drop box.</h2>
          <p className="max-w-xl text-lg leading-relaxed text-muted-foreground">
            You don’t chat, wait or tweak prompts. Drop the task in, leave, and come back to a finished answer.
          </p>
        </div>
        <div className="grid gap-8 md:grid-cols-3 md:gap-10">
          <Feature title="Send, then leave">
            Close the tab. You get a ping when the answer is ready, wherever you are in the app.
          </Feature>
          <Feature title="Made to copy by hand">
            Worksheets come back as a file with every equation solved line by line. Download it, copy it onto paper, done.
          </Feature>
          <Feature title="You decide">
            Every answer waits for your review. If it’s off, send it back with a note.
          </Feature>
        </div>
      </section>

      {/* Test prep concept */}
      <section id="test-prep" className="wrap scroll-mt-24">
        <div className="overflow-hidden rounded-2xl bg-ink p-6 text-background sm:p-10 lg:p-14">
          <div className="max-w-3xl">
            <span className="pill bg-highlight text-ink">Coming next</span>
            <h2 className="mt-5 text-headline">Test prep that starts from what you already did.</h2>
            <p className="mt-5 text-lg leading-relaxed text-ink-muted">
              Solve your homework with JustPass. JustPass collects it, adds whatever else you send, and teaches you the bare minimum to pass, the way you like to learn.
            </p>
          </div>

          <div className="mt-10 grid gap-5 lg:grid-cols-2 lg:gap-8">
            <div className="grid gap-3">
              <SourceCard title="Homework" body="Done in JustPass or pasted from any other AI." />
              <SourceCard title="Notes, presentations, PDFs" body="Whatever your teacher gave you." />
              <SourceCard title="Anything else you send" body="The more it sees, the better it knows what you’ll be asked." />
            </div>

            <div className="rounded-xl bg-card p-5 text-foreground sm:p-7">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-2xl">Your minimum to pass</h3>
                <span className="pill bg-sand text-muted-foreground">Sample preview</span>
              </div>
              <Topic title="Quadratic roots" status="Learn first" statusClass="text-primary-ink" />
              <Topic title="Complex roots" status="Quick read" />
              <Topic title="Vertex form" status="Skip for now" statusClass="text-muted-foreground" />
              <p className="mt-5 text-sm leading-relaxed text-muted-foreground">
                Taught as short summaries, flashcards or practice questions, whichever you learn best from.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Final CTA */}
      <section className="wrap flex flex-col items-center gap-6 text-center">
        <h2 className="text-headline">Get your evenings back.</h2>
        <div className="flex flex-wrap justify-center gap-3">
          <Button asChild size="lg" className="h-12 px-6">
            <Link to={isAuthenticated ? '/new' : '/login'}>{primaryLabel}</Link>
          </Button>
          <Button asChild size="lg" variant="outline" className="h-12 px-6">
            <Link to="/login">Log in</Link>
          </Button>
        </div>
      </section>
    </div>
  )
}

function Feature({ title, children }: { title: string; children: string }) {
  return (
    <article className="border-t-[3px] border-foreground pt-4">
      <h3 className="text-2xl">{title}</h3>
      <p className="mt-2 leading-relaxed text-muted-foreground">{children}</p>
    </article>
  )
}

function SourceCard({ title, body }: { title: string; body: string }) {
  return (
    <article className="rounded-xl bg-ink-soft p-5">
      <h3 className="font-semibold text-background">{title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-ink-muted">{body}</p>
    </article>
  )
}

function Topic({ title, status, statusClass = '' }: { title: string; status: string; statusClass?: string }) {
  return (
    <div className="flex flex-wrap justify-between gap-3 border-t border-border pt-3 first:mt-5">
      <span>{title}</span>
      <strong className={statusClass}>{status}</strong>
    </div>
  )
}
