import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { GradesFocus, LabCard, PassPlan, TeacherTopics } from '@/components/lab'
import { HeroPhone, ReviewMock, SnapMock, SolvingMock } from '@/components/mocks'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STEPS: [string, string, ReactNode][] = [
  ['Snap it or paste it', 'A photo of the worksheet, a screenshot, or the task as text.', <SnapMock key="s" />],
  ['AI does it', 'Worked step by step, with the math written out properly.', <SolvingMock key="m" />],
  ['You review it', 'Accept it, or push back and get a revised answer.', <ReviewMock key="r" />],
]

const ROADMAP = [
  ['Now', 'Homework autopilot', 'Live. Send a task, get a worked answer, review it.', 'border-volt bg-volt text-ink'],
  ['Next', 'Test prep that knows your teacher', 'Predicts what is on the next test from your homework and grades.', 'border-pink'],
  ['Later', 'Pass mode and grade tracking', 'The bare minimum for subjects you only need to pass.', 'border-input'],
] as const

export default function Landing() {
  return (
    <div className="space-y-24 sm:space-y-36">
      <section className="grid items-center gap-12 pt-2 lg:grid-cols-[1.25fr_1fr]">
        <div>
          <span className="sticker bg-volt text-ink">Homework autopilot is live</span>
          <h1 className="mt-6 text-[4.1rem] sm:text-[7.5rem]">Do less school. Still pass.</h1>
          <p className="mt-6 max-w-[34ch] text-xl text-muted-foreground">
            Send a photo of your homework or paste the task. AI does it, you review the result. That's the whole trick.
          </p>
          <div className="mt-9 flex flex-col gap-3 sm:flex-row">
            <Button asChild size="lg" className="h-14 px-7 text-lg">
              <Link to="/new">Send your first task</Link>
            </Button>
            <Button asChild size="lg" variant="ghost" className="h-14 text-lg">
              <a href="#lab">See what's coming</a>
            </Button>
          </div>
        </div>
        <div className="relative py-6">
          <HeroPhone />
          <span aria-hidden="true" className="sticker absolute top-0 left-0 rotate-[-8deg] bg-foreground text-ink sm:left-[8%]">
            Photo in
          </span>
          <span aria-hidden="true" className="sticker absolute right-0 bottom-0 rotate-[5deg] bg-volt text-ink sm:right-[6%]">
            ~20 min back
          </span>
        </div>
      </section>

      <section aria-labelledby="live">
        <Heading id="live" tag="Live now" tagClass="bg-volt text-ink" title="Homework autopilot" sub="Three steps. The middle one isn't yours." />
        <ol className="mt-10 grid gap-5 md:grid-cols-3">
          {STEPS.map(([title, body, mock], i) => (
            <li key={title} className="flex flex-col rounded-2xl border-2 border-border bg-card p-5">
              <span className="font-display text-6xl leading-none font-extrabold text-volt" aria-hidden="true">
                {i + 1}
              </span>
              <h3 className="mt-3 text-2xl">{title}</h3>
              <p className="mt-1 mb-5 text-muted-foreground">{body}</p>
              <div className="mt-auto">{mock}</div>
            </li>
          ))}
        </ol>
      </section>

      <section id="lab" aria-labelledby="lab-title" className="scroll-mt-20">
        <Heading
          id="lab-title"
          tag="In the lab"
          tagClass="bg-pink text-ink"
          title="Next: it learns your teacher."
          sub="It learns from your homework, your grades and what your teacher keeps coming back to. We're building this. Here's what it'll look like."
        />
        <div className="mt-10 grid gap-5 lg:grid-cols-3">
          <LabCard id="teacher" title="Test prep that knows your teacher" pitch="Ranks topics by how often your teacher brings them up, so you study what's actually on the test.">
            <TeacherTopics />
          </LabCard>
          <LabCard id="pass-mode" title="Pass mode" pitch="For subjects you don't care about: a short prep with only what's likely to show up. Not the whole textbook.">
            <PassPlan />
          </LabCard>
          <LabCard id="grades" title="Grades in, focus out" pitch="Connect your grades and see where you're safe and where you're at risk.">
            <GradesFocus />
          </LabCard>
        </div>
      </section>

      <section aria-labelledby="roadmap">
        <h2 id="roadmap" className="text-4xl sm:text-5xl">
          Where this is going
        </h2>
        <ol className="mt-8 grid gap-4 md:grid-cols-3">
          {ROADMAP.map(([when, what, body, cls]) => (
            <li key={when} className="rounded-2xl border-2 border-border bg-card p-5">
              <span className={cn('inline-block rounded-lg border-2 px-2.5 font-display text-lg font-extrabold', cls)}>{when}</span>
              <h3 className="mt-3 text-2xl">{what}</h3>
              <p className="mt-1 text-muted-foreground">{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="rounded-2xl bg-volt p-7 text-ink sm:p-14 [&_:focus-visible]:outline-ink">
        <h2 className="max-w-[16ch] text-5xl sm:text-7xl">Less time on school stuff that doesn't matter.</h2>
        <p className="mt-5 max-w-[40ch] text-xl font-semibold">More time for what you actually care about.</p>
        <Button asChild size="lg" className="mt-8 h-14 bg-ink px-7 text-lg text-volt hover:bg-ink/90">
          <Link to="/new">Send your first task</Link>
        </Button>
      </section>
    </div>
  )
}

function Heading({ id, tag, tagClass, title, sub }: { id: string; tag: string; tagClass: string; title: string; sub: string }) {
  return (
    <div className="max-w-2xl">
      <span className={cn('sticker text-sm', tagClass)}>{tag}</span>
      <h2 id={id} className="mt-4 text-5xl sm:text-6xl">
        {title}
      </h2>
      <p className="mt-4 text-lg text-muted-foreground">{sub}</p>
    </div>
  )
}
