import { Camera, Check, MessageSquareWarning } from 'lucide-react'
import { cn } from '@/lib/utils'

// Product mocks for the landing page, drawn in HTML/CSS. Decorative: the copy next to them says the same thing.

const RULED = 'repeating-linear-gradient(transparent 0 27px, #b9c6e6 27px 28px)'

function Worksheet({ className }: { className?: string }) {
  return (
    <div className={cn('rotate-2 rounded-sm bg-[#fdfcf7] p-3 pl-6 font-hand text-[1.35rem] leading-7 text-[#26306b] shadow-[0_8px_0_#000]', className)} style={{ backgroundImage: RULED, backgroundPositionY: '10px' }}>
      <p>4b) Solve 2x² − 8x + 6 = 0</p>
      <p className="text-[#8a90b8]">show your working!!</p>
    </div>
  )
}

const Var = ({ v }: { v: string }) => <i className="font-serif">{v}</i>

export function HeroPhone() {
  return (
    <div aria-hidden="true" className="relative mx-auto w-[min(300px,82vw)] rotate-2 lg:w-[340px] rounded-[2.6rem] border-[9px] border-black bg-ink p-3 pb-5 shadow-[10px_10px_0_var(--volt)]">
      <div className="mx-auto mb-3 h-5 w-24 rounded-full bg-black" />
      <div className="relative overflow-hidden rounded-xl">
        <Worksheet className="rotate-0 shadow-none" />
        <div className="scan absolute inset-x-0 h-1 bg-volt shadow-[0_0_18px_4px_var(--volt)]" />
      </div>
      <div className="relative mt-3 rounded-xl bg-paper p-3 text-[0.82rem] leading-snug text-paper-ink">
        <p className="font-display text-base font-extrabold">Step by step</p>
        <ol className="mt-1.5 space-y-1.5">
          <li>
            <b>1.</b> Divide by 2: <Var v="x" />² − 4<Var v="x" /> + 3 = 0
          </li>
          <li>
            <b>2.</b> Factor: (<Var v="x" /> − 1)(<Var v="x" /> − 3) = 0
          </li>
          <li>
            <b>3.</b> So <span className="rounded bg-volt px-1 font-bold"><Var v="x" /> = 1 or <Var v="x" /> = 3</span>
          </li>
        </ol>
        <div className="mt-3 flex gap-2">
          <span className="flex-1 rounded-lg bg-paper-ink py-1.5 text-center font-bold text-paper">Accept</span>
          <span className="flex-1 rounded-lg border-2 border-paper-ink py-1 text-center font-bold">Push back</span>
        </div>
        <span className="stamp absolute -top-5 right-2 bg-paper/80 text-2xl text-[#1f8a3b]">PASS</span>
      </div>
    </div>
  )
}

export function SnapMock() {
  return (
    <div aria-hidden="true" className="relative rounded-xl bg-black p-4">
      <div className="relative p-3">
        {['top-0 left-0 border-t-4 border-l-4', 'top-0 right-0 border-t-4 border-r-4', 'bottom-0 left-0 border-b-4 border-l-4', 'bottom-0 right-0 border-r-4 border-b-4'].map((c) => (
          <span key={c} className={cn('absolute size-5 border-volt', c)} />
        ))}
        <Worksheet className="-rotate-1 text-lg leading-6 shadow-none" />
      </div>
      <div className="mt-3 flex justify-center">
        <span className="grid size-12 place-items-center rounded-full border-4 border-foreground">
          <Camera className="size-5" />
        </span>
      </div>
    </div>
  )
}

export function SolvingMock() {
  return (
    <div aria-hidden="true" className="rounded-xl bg-raised p-4">
      <p className="flex items-center gap-2 font-bold text-volt">
        <span className="size-2.5 animate-pulse rounded-full bg-volt" /> Solving, step 2 of 3
      </p>
      <div className="mt-3 space-y-2">
        <div className="h-2.5 w-full rounded-full bg-volt" />
        <div className="h-2.5 w-3/4 rounded-full bg-volt/60" />
        <div className="h-2.5 w-1/3 rounded-full bg-foreground/20" />
      </div>
      <p className="mt-3 text-sm text-muted-foreground">Go do something else. You get a ping when it's done.</p>
    </div>
  )
}

export function ReviewMock() {
  return (
    <div aria-hidden="true" className="rounded-xl bg-paper p-4 text-paper-ink">
      <p className="font-semibold">
        <Var v="x" /> = 1 or <Var v="x" /> = 3
      </p>
      <div className="mt-3 flex flex-col gap-2">
        <span className="flex items-center justify-center gap-1.5 rounded-lg bg-paper-ink py-2 font-bold text-paper">
          <Check className="size-4" /> Accept
        </span>
        <span className="flex items-center justify-center gap-1.5 rounded-lg border-2 border-paper-ink py-1.5 font-bold">
          <MessageSquareWarning className="size-4" /> "Step 2 skips a bit"
        </span>
      </div>
    </div>
  )
}
