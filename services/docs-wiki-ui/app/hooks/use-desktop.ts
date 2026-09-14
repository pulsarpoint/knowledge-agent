import * as React from "react"

// Matches Tailwind's `lg` breakpoint (1024px) — the same threshold DocView's
// TOC already used via its `lg:`-prefixed CSS before this hook existed.
// Mirrors `use-mobile.tsx`'s `useIsMobile` structure (guarded matchMedia +
// resize listener, read `window.innerWidth` for the actual value) but as its
// own hook rather than a mobile/desktop inversion of it: DocView's `lg`
// threshold and Sidebar's `md` threshold are independent breakpoints that
// should be free to diverge.
const DESKTOP_BREAKPOINT = 1024

export function useIsDesktop() {
  const [isDesktop, setIsDesktop] = React.useState<boolean | undefined>(undefined)

  React.useEffect(() => {
    const mql = window.matchMedia(`(min-width: ${DESKTOP_BREAKPOINT}px)`)
    const onChange = () => {
      setIsDesktop(window.innerWidth >= DESKTOP_BREAKPOINT)
    }
    mql.addEventListener("change", onChange)
    setIsDesktop(window.innerWidth >= DESKTOP_BREAKPOINT)
    return () => mql.removeEventListener("change", onChange)
  }, [])

  return !!isDesktop
}
