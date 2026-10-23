import { Link } from 'react-router'
import { GradesFocus, LabCard, PassPlan, TeacherTopics } from '@/components/lab'
import { Button } from '@/components/ui/button'

export default function Prep() {
  return (
    <div>
      <div className="max-w-2xl">
        <span className="sticker bg-pink text-sm text-ink">In the lab</span>
        <h1 className="mt-4 text-6xl sm:text-7xl">Test prep</h1>
        <p className="mt-4 text-lg text-muted-foreground">
          We're building this. Here's what it'll look like: prep for the tests that matter, and the bare minimum for the ones that don't.
        </p>
      </div>

      <section aria-labelledby="trains" className="mt-8 flex flex-col gap-4 rounded-2xl border-2 border-volt bg-card p-5 sm:flex-row sm:items-center sm:p-6">
        <div className="flex-1">
          <h2 id="trains" className="text-2xl">
            Your homework trains this
          </h2>
          <p className="mt-1 text-muted-foreground">
            Every task you send and every answer you accept shows JustPass what your teachers care about. When test prep lands, it starts from that.
          </p>
        </div>
        <Button asChild size="lg">
          <Link to="/new">Send a task</Link>
        </Button>
      </section>

      <h2 className="sr-only">Previews</h2>
      <div className="mt-8 grid gap-5 lg:grid-cols-3">
        <LabCard id="teacher" title="Your next test, predicted" pitch="Topics ranked by how often your teacher brings them up in homework and class notes.">
          <TeacherTopics />
        </LabCard>
        <LabCard id="pass-mode" title="Pass mode" pitch="Mark a subject as 'just pass' and get a short plan with only what's likely to show up.">
          <PassPlan />
        </LabCard>
        <LabCard id="grades" title="Connect your grades" pitch="See where you're safe and where you're at risk, and where your time should go this week.">
          <GradesFocus />
        </LabCard>
      </div>
    </div>
  )
}
