import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { RANGES } from "../state";
import { cx } from "./ui";

function ClockIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" />
      <path d="M10 5.5V10l3 2" />
    </svg>
  );
}

/**
 * Compact time range picker: a button showing the current range that opens a
 * listbox. Keyboard: arrows/Home/End move, Enter or Space picks, Escape closes.
 */
export function TimeRangeSelect({ value, onChange }: { value: string; onChange: (range: string) => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const selected = RANGES.findIndex((r) => r.id === value);
  const [active, setActive] = useState(Math.max(selected, 0));
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);

  // A range set by URL that is not in the list (the API accepts more) still gets a readable label.
  const label = selected >= 0 ? RANGES[selected].long : `Last ${value}`;

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const openMenu = () => {
    setActive(Math.max(selected, 0));
    setOpen(true);
  };
  const choose = (i: number) => {
    setOpen(false);
    button.current?.focus();
    if (RANGES[i].id !== value) onChange(RANGES[i].id);
  };

  const onButtonKey = (e: KeyboardEvent) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      openMenu();
    }
  };
  const onListKey = (e: KeyboardEvent) => {
    const last = RANGES.length - 1;
    const moves: Record<string, number> = {
      ArrowDown: Math.min(active + 1, last),
      ArrowUp: Math.max(active - 1, 0),
      Home: 0,
      End: last,
    };
    if (e.key in moves) {
      e.preventDefault();
      setActive(moves[e.key]);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choose(active);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      button.current?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={box} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-label={`Time range: ${label}`}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onButtonKey}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-rule-strong bg-surface px-2.5 text-[13px] font-medium whitespace-nowrap text-ink hover:bg-sunken"
      >
        <span className="text-muted">
          <ClockIcon />
        </span>
        {label}
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className={cx("text-muted transition-transform", open && "rotate-180")}>
          <path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label="Time range"
          aria-activedescendant={`${id}-opt-${active}`}
          onKeyDown={onListKey}
          className="absolute top-9 left-0 z-30 min-w-[11rem] rounded-md border border-rule-strong bg-surface py-1 shadow-lg focus:outline-none"
        >
          {RANGES.map((r, i) => (
            <li
              key={r.id}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === selected}
              onClick={() => choose(i)}
              onMouseEnter={() => setActive(i)}
              className={cx(
                "flex cursor-pointer items-center justify-between gap-4 px-3 py-1.5 text-[13px]",
                i > 0 && RANGES[i - 1].group !== r.group && "mt-1 border-t border-rule pt-2",
                i === active && "bg-sunken",
                i === selected ? "font-medium text-ink" : "text-ink-2",
              )}
            >
              {r.long}
              {i === selected && (
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="text-accent">
                  <path d="M3 7.5l2.5 2.5L11 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
