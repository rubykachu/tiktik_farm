import { useRef, useState, type ReactNode } from "react";

// Reusable in place of the native `title` attribute, which on most browsers
// takes 500ms-1s of hovering before anything shows — too slow for a UI
// element whose whole point is "quick clarification". Also works on touch
// (tap to toggle), which `title` never did.

const SHOW_DELAY_MS = 80;

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "bottom";
  // Caller supplies its own display utility (e.g. "inline-flex items-center"
  // for an icon, "block truncate w-full" for a cell whose text should
  // ellipsis) — no display class is hardcoded here.
  className?: string;
}

export default function Tooltip({ content, children, side = "top", className }: TooltipProps) {
  const [open, setOpen] = useState(false);
  const timerRef = useRef<number | null>(null);

  function show() {
    timerRef.current = window.setTimeout(() => setOpen(true), SHOW_DELAY_MS);
  }
  function hide() {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    setOpen(false);
  }

  const positionClass =
    side === "top" ? "bottom-full left-1/2 -translate-x-1/2 mb-1.5" : "top-full left-1/2 -translate-x-1/2 mt-1.5";
  const arrowClass =
    side === "top"
      ? "top-full left-1/2 -translate-x-1/2 border-t-slate-800"
      : "bottom-full left-1/2 -translate-x-1/2 border-b-slate-800";

  return (
    <span
      className={`relative ${className ?? "inline-flex"}`}
      onMouseEnter={show}
      onMouseLeave={hide}
      onClick={(e) => {
        e.stopPropagation();
        setOpen((o) => !o);
      }}
    >
      {children}
      {open && (
        <span
          role="tooltip"
          // whitespace-normal overrides `white-space` inherited from any
          // ancestor (e.g. a table header's whitespace-nowrap) — without it
          // the bubble can't wrap and the text overflows past its own
          // background instead of respecting max-w-xs.
          className={`absolute z-50 ${positionClass} w-max max-w-xs whitespace-normal px-2 py-1.5 rounded bg-slate-800 text-white text-xs leading-snug shadow-lg pointer-events-none`}
        >
          {content}
          <span className={`absolute ${arrowClass} border-4 border-transparent`} />
        </span>
      )}
    </span>
  );
}
