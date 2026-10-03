import { useLayoutEffect, useRef, useState } from "react";

/**
 * Width of a container in px, so SVG charts render at their real size (crisp text on
 * phones). Measured in a layout effect, i.e. before the first paint, then kept up to
 * date by a ResizeObserver. jsdom has no layout, so tests get the fallback.
 */
export function useElementWidth<E extends HTMLElement>(fallback = 640) {
  const ref = useRef<E>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      if (el.clientWidth > 0) setWidth(el.clientWidth);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}
