import { Link } from 'react-router'
import { Button } from '@/components/ui/button'

const STEPS = [
  ['Ask', 'Type the question or photograph the page. Handwriting is fine.'],
  ['Get on with your night', 'We let you know when the solution is ready, wherever you are in the app.'],
  ['Check every step', 'Accept the answer, or say what is off and get a revised one.'],
]

export default function Landing() {
  return (
    <div className="space-y-16 sm:space-y-24">
      <section className="grid items-center gap-10 sm:grid-cols-[1.2fr_1fr] lg:gap-16">
        <div>
          <h1 className="text-[2.6rem] sm:text-6xl">Homework help that shows its work.</h1>
          <p className="mt-5 max-w-[38ch] text-lg text-muted-foreground">
            Snap a photo or type the question. JustPass writes a step-by-step solution you can follow, with the math set out properly.
          </p>
          <Button asChild size="lg" className="mt-8 w-full sm:w-auto">
            <Link to="/new">Ask your first question</Link>
          </Button>
        </div>
        <WorkedExample />
      </section>

      <section aria-labelledby="how">
        <h2 id="how" className="text-2xl sm:text-3xl">How it works</h2>
        <ol className="mt-6 grid gap-6 sm:grid-cols-3">
          {STEPS.map(([title, body], i) => (
            <li key={title} className="flex gap-4 sm:flex-col sm:gap-2">
              <span aria-hidden="true" className="grid size-11 shrink-0 place-items-center rounded-full border-2 border-ink text-lg font-extrabold text-ink">
                {i + 1}
              </span>
              <div>
                <h3 className="text-lg">{title}</h3>
                <p className="mt-1 text-muted-foreground">{body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

/** A hand-set worked solution: the product in one glance, without loading KaTeX on the landing page. */
function WorkedExample() {
  return (
    <figure className="-rotate-1 rounded-xl border-2 border-ink bg-card p-5 shadow-[6px_6px_0_var(--ink)] sm:p-6">
      <figcaption className="font-bold text-ink">
        Solve{' '}
        <span className="font-serif text-lg font-normal">
          <i>x</i>² − 5<i>x</i> + 6 = 0
        </span>
      </figcaption>
      <ol className="mt-4 space-y-3 border-l-2 border-hl-yellow pl-4 text-[0.98rem]">
        <li>
          <span className="text-muted-foreground">Factor: two numbers that multiply to 6 and add to −5.</span>
          <span className="mt-1 block font-serif text-lg">(<i>x</i> − 2)(<i>x</i> − 3) = 0</span>
        </li>
        <li>
          <span className="text-muted-foreground">A product is zero when one factor is zero.</span>
        </li>
      </ol>
      <p className="mt-4 font-bold">
        <span className="hl bg-hl-yellow px-2 py-0.5 font-serif text-lg">
          <i>x</i> = 2 or <i>x</i> = 3
        </span>
      </p>
    </figure>
  )
}
