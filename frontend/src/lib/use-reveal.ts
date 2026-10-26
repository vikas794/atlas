import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useRef, type RefObject } from 'react'

import { motionAllowed } from './motion'

gsap.registerPlugin(useGSAP)

/**
 * Staggered entrance for the direct `[data-reveal]` children inside the returned container ref.
 * Re-runs when `deps` change (e.g. the list length). Skipped under reduced motion and in tests.
 */
export function useReveal<T extends HTMLElement>(deps: unknown[] = []): RefObject<T | null> {
  const scope = useRef<T | null>(null)
  useGSAP(
    () => {
      if (!motionAllowed() || !scope.current) return
      const targets = scope.current.querySelectorAll('[data-reveal]')
      if (targets.length === 0) return
      gsap.fromTo(
        targets,
        { opacity: 0, y: 18 },
        {
          opacity: 1,
          y: 0,
          duration: 0.7,
          ease: 'power3.out',
          stagger: 0.06,
          clearProps: 'transform,opacity',
        },
      )
    },
    { scope, dependencies: deps },
  )
  return scope
}
