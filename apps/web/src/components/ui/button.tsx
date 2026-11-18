
import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from 'cn'
import { Slot } from 'radix-ui'

const buttonVariants = cva(
  [
    'group/button inline-flex shrink-0 items-center justify-center gap-2',
    'rounded-lg border-2 border-transparent',
    'font-semibold text-sm whitespace-nowrap',
    'transition-all duration-150',
    'active:translate-y-px',
    'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
    'aria-disabled:pointer-events-none aria-disabled:opacity-50',
    'focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-ring',
    '[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  ],
  {
    variants: {
      variant: {
        default:
          'btn shadow-[3px_3px_0_var(--ink)] hover:shadow-[2px_2px_0_var(--ink)] active:translate-x-px active:translate-y-px active:shadow-none',

        outline:
          'btn-outline hover:bg-sand-hover',

        secondary:
          'border-line bg-sand text-foreground hover:bg-sand-hover',

        ghost:
          'border-transparent bg-transparent text-foreground hover:bg-sand hover:border-line',

        destructive:
          'border-destructive bg-destructive text-white hover:brightness-90',

        link:
          'min-h-0 border-transparent bg-transparent p-0 text-primary-ink underline-offset-4 hover:underline',
      },

      size: {
        default: 'h-12 px-6 text-[0.95rem]',
        sm: 'btn-sm text-sm',
        lg: 'h-14 px-7 text-base',
        xs: 'h-8 rounded-md px-3 text-xs',
        icon: 'size-11 p-0',
        'icon-sm': 'size-9 p-0',
        'icon-lg': 'size-12 p-0',
      },
    },

    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

type ButtonProps =
  React.ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean
  }

function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  type,
  ...props
}: ButtonProps) {
  const Comp = asChild ? Slot.Root : 'button'

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      type={asChild ? type : (type ?? 'button')}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { Button, buttonVariants }
