import type { ReactNode } from 'react'
import { Bell, Check } from 'lucide-react'
import { useNotify } from '@/lib/notify'
import { cn } from '@/lib/utils'

/**
 * The one treatment for every feature that is not built yet: hazard tape, an "In the lab" sticker,
 * and sample UI behind a hatched "Sample" frame. The only live control is "Notify me".
 */
export function LabCard({ id, title, pitch, children, className }: { id: string; title: string; pitch: string; children: ReactNode; className?: string }) {
  return (
    <article aria-labelledby={`lab-${id}`} className={cn('flex flex-col overflow-hidden rounded-2xl border-2 border-pink/70 bg-card', className)}>
      <div aria-hidden="true" className="tape h-3.5 shrink-0" />
      <div className="flex flex-1 flex-col p-5 sm:p-6">
        <span className="sticker self-start bg-pink text-sm text-ink">In the lab</span>
        <h3 id={`lab-${id}`} className="mt-4 text-3xl">
          {title}
        </h3>
        <p className="mt-2 text-muted-foreground">{pitch}</p>
        <figure className="hatch relative mt-6 mb-5 rounded-xl border-2 border-dashed border-pink/50 p-4 pt-5">
          <figcaption className="absolute -top-3 left-3 rounded-md bg-pink px-2 text-sm leading-6 font-bold text-ink">Sample</figcaption>
          {children}
        </figure>
        <NotifyToggle id={id} />
      </div>
    </article>
  )
}

/** Toggle the browser's saved interest in the feature identified by id. */
function NotifyToggle({ id }: { id: string }) {
  const [on, toggle] = useNotify(id)
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={toggle}
      className={cn(
        'mt-auto inline-flex h-11 items-center justify-center gap-2 self-start rounded-xl border-2 px-4 font-bold',
        on ? 'border-pink bg-pink text-ink' : 'border-pink/70 text-pink hover:bg-pink/10',
      )}
    >
      {on ? <Check className="size-4" aria-hidden="true" /> : <Bell className="size-4" aria-hidden="true" />}
      {on ? 'Saved on this device' : 'Notify me'}
    </button>
  )
}

const TOPICS = [
  ['Cellular respiration', 9],
  ['Mitosis vs meiosis', 6],
  ['Reading enzyme graphs', 4],
  ['Osmosis', 2],
] as const

/** Render fixed sample topic rankings for the test prep preview. */
export function TeacherTopics() {
  return (
    <div>
      <p className="font-display text-xl font-extrabold">Likely on Friday's bio test</p>
      <ol className="mt-3 space-y-2.5">
        {TOPICS.map(([topic, n], i) => (
          <li key={topic}>
            <div className="flex justify-between gap-3 text-sm">
              <span className="font-semibold">{topic}</span>
              <span className="text-muted-foreground">{n} teacher mentions</span>
            </div>
            <div className="mt-1 h-2 rounded-full bg-raised">
              <div className={cn('h-full rounded-full', i === 0 ? 'bg-volt' : 'bg-foreground/70')} style={{ width: `${(n / 9) * 100}%` }} />
            </div>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-sm text-muted-foreground">Based on 14 homework tasks and 3 graded quizzes.</p>
    </div>
  )
}

const PLAN = [
  ['15 min', 'Balancing equations', 'On every test so far'],
  ['15 min', 'Moles and molar mass', 'Your teacher brought it up 5 times'],
  ['10 min', 'Practice: 6 likely questions', 'Worked answers included'],
] as const

/** Render a fixed sample 40-minute chemistry plan for the pass mode preview. */
export function PassPlan() {
  return (
    <div>
      <p className="font-display text-xl font-extrabold">Chemistry: 40-minute plan to a pass</p>
      <p className="text-sm text-muted-foreground">Target 50%. You marked this subject as "just pass".</p>
      <ol className="mt-3 space-y-2">
        {PLAN.map(([time, what, why]) => (
          <li key={what} className="flex gap-3 rounded-lg bg-raised p-2.5">
            <span className="w-14 shrink-0 font-display font-extrabold text-volt">{time}</span>
            <span>
              <span className="block font-semibold">{what}</span>
              <span className="block text-sm text-muted-foreground">{why}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-sm text-muted-foreground">
        Skipped: <s>organic naming</s>, <s>electrochemistry</s> and 7 more topics that have not shown up on a test.
      </p>
    </div>
  )
}

const GRADES = [
  ['Math', 'B+', 'Safe', 'bg-volt text-ink'],
  ['English', 'B', 'Safe', 'bg-volt text-ink'],
  ['History', 'C', 'Watch', 'border-2 border-foreground/70'],
  ['Chemistry', 'D', 'At risk', 'bg-coral text-ink'],
] as const

/** Render sample grades and study priorities for the grade tracking preview. */
export function GradesFocus() {
  return (
    <div>
      <p className="font-display text-xl font-extrabold">Where you stand this term</p>
      <ul className="mt-3 divide-y divide-border">
        {GRADES.map(([subject, grade, status, cls]) => (
          <li key={subject} className="flex items-center gap-3 py-2">
            <span className="flex-1 font-semibold">{subject}</span>
            <span className="w-8 font-display text-lg font-extrabold">{grade}</span>
            <span className={cn('w-20 rounded-md py-0.5 text-center text-sm font-bold', cls)}>{status}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm text-muted-foreground">This week: 40 minutes on chemistry. History can wait.</p>
    </div>
  )
}
