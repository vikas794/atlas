import gsap from 'gsap'
import { useEffect, useRef } from 'react'

import { motionAllowed } from '../../lib/motion'

/** A number that tweens from its previous value to `value` (instant under reduced motion). */
export function CountUp({ value }: { value: number }) {
  const ref = useRef<HTMLSpanElement>(null)
  const shown = useRef(0)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (!motionAllowed()) {
      shown.current = value
      node.textContent = String(value)
      return
    }
    const state = { n: shown.current }
    const tween = gsap.to(state, {
      n: value,
      duration: 0.9,
      ease: 'power3.out',
      onUpdate: () => {
        shown.current = state.n
        node.textContent = String(Math.round(state.n))
      },
    })
    return () => {
      tween.kill()
    }
  }, [value])

  return (
    <span ref={ref} className="tabular">
      {value}
    </span>
  )
}
