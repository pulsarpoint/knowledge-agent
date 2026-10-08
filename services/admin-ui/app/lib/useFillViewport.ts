import { useLayoutEffect, useState, type CSSProperties } from 'react';

const rem = () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;

/** A container that fills the screen from where it starts down to `bottomGap` rem above the bottom (the main padding),
 * so a page's own scrolling panes and message box stay on screen whatever sits above it. Measured, not hard-coded: it
 * follows window resizes and content above it (an alert, a wrapped header). Never shorter than `floor` rem (room for the header, the
 * message box and the pane's own minimum height): on a tiny screen the page scrolls instead of collapsing the panes. `ref` is a callback ref: the container may mount later
 * (after a page loads). */
export function useFillViewport<T extends HTMLElement>(bottomGap = 1.5, floor = 26) {
  const [element, ref] = useState<T | null>(null);
  const [height, setHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    if (!element) return;
    const measure = () => {
      const top = element.getBoundingClientRect().top + window.scrollY;  // where it starts on the page
      const unit = rem();
      setHeight(Math.floor(Math.max(floor * unit, window.innerHeight - top - bottomGap * unit)));
    };
    measure();
    window.addEventListener('resize', measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(document.body);  // content above the container moves it down or up
    if (element.parentElement) observer?.observe(element.parentElement);  // and shrinking content above it (an alert going away)
    return () => { window.removeEventListener('resize', measure); observer?.disconnect(); };
  }, [element, bottomGap, floor]);
  const style: CSSProperties = height === null ? {} : { height: `${height}px` };
  return { ref, style };
}
