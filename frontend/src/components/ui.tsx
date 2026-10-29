import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { Link } from "react-router";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

// ---------------------------------------------------------------- buttons

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-white hover:brightness-110 dark:text-canvas",
  secondary: "bg-surface text-ink border border-rule-strong hover:bg-sunken",
  ghost: "text-ink-2 hover:bg-sunken",
  danger: "bg-surface text-danger border border-rule-strong hover:bg-danger-soft",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      type="button"
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none",
        size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-9 px-3.5",
        variants[variant],
        className,
      )}
    />
  );
}

// ---------------------------------------------------------------- inputs

const inputCls =
  "h-9 w-full rounded-md border border-rule-strong bg-surface px-2.5 text-ink placeholder:text-faint focus:border-accent focus:outline-none";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputCls, className)} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={cx(inputCls, "pr-7", className)}>
      {children}
    </select>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactElement<{ id?: string; "aria-describedby"?: string }>; className?: string }) {
  const id = useId();
  const hintId = `${id}-hint`;
  const control = isValidElement(children)
    ? cloneElement(children, { id: children.props.id ?? id, ...(hint ? { "aria-describedby": hintId } : {}) })
    : children;
  return (
    <div className={cx("block", className)}>
      <label htmlFor={isValidElement(children) ? (children.props.id ?? id) : undefined} className="mb-1 block text-[13px] font-medium text-ink-2">
        {label}
      </label>
      {control}
      {hint && (
        <p id={hintId} className="mt-1 text-[12.5px] text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2.5 select-none">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx("relative h-5 w-9 rounded-full transition-colors", checked ? "bg-accent" : "bg-rule-strong")}
      >
        <span
          className={cx(
            "absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
            checked && "translate-x-4",
          )}
        />
      </button>
      <span className="text-ink-2">{label}</span>
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { id: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-rule-strong bg-surface p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="radio"
          aria-checked={value === o.id}
          onClick={() => onChange(o.id)}
          className={cx(
            "h-7 whitespace-nowrap rounded-[5px] px-2.5 text-[13px] font-medium transition-colors num",
            value === o.id ? "bg-ink text-canvas" : "text-ink-2 hover:bg-sunken",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- containers

export function Panel({ title, actions, children, className, flush }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={cx("rounded-lg border border-rule bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-rule px-4 py-2.5">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[22px] leading-tight font-semibold tracking-[-0.01em]">{title}</h1>
        {subtitle && <div className="mt-1 text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-xl border border-rule bg-surface p-0 text-ink shadow-2xl backdrop:bg-[#0b1118]/50",
        wide ? "max-w-3xl" : "max-w-lg",
      )}
    >
      {open && (
        <div>
          <header className="flex items-center justify-between border-b border-rule px-5 py-3.5">
            <h2 className="text-[16px] font-semibold">{title}</h2>
            <button type="button" aria-label="Close" onClick={onClose} className="rounded p-1 text-muted hover:bg-sunken hover:text-ink">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
            </button>
          </header>
          <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
          {footer && <footer className="flex justify-end gap-2 border-t border-rule px-5 py-3">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}

// ---------------------------------------------------------------- feedback

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded-md border border-danger/40 bg-danger-soft px-3 py-2 text-danger">{msg}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-6 py-12 text-center">
      <div className="mx-auto mb-3 w-24" aria-hidden="true">
        <svg viewBox="0 0 96 32" className="w-full text-rule-strong">
          <path d="M2 16h92" stroke="currentColor" strokeWidth="1.5" />
          <path d="M8 16c6-4 10-9 16-9s9 6 15 6 10-8 16-8 9 5 15 7 10-2 18 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="3 3" />
        </svg>
      </div>
      <div className="font-semibold">{title}</div>
      {children && <div className="mx-auto mt-1.5 max-w-md text-muted">{children}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-muted" role="status">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-rule-strong border-t-accent" />
      {label}
    </div>
  );
}

export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "accent" | "ok" | "warn" | "danger"; title?: string }) {
  const tones = {
    neutral: "bg-sunken text-ink-2",
    accent: "bg-accent-soft text-accent",
    ok: "bg-ok/12 text-ok",
    warn: "bg-warn/12 text-warn",
    danger: "bg-danger-soft text-danger",
  };
  return (
    <span title={title} className={cx("inline-flex items-center rounded px-1.5 py-px text-[12px] font-medium whitespace-nowrap", tones[tone])}>
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- AS label

export function AsName({ asn, name, country, handle, to, size = "md" }: { asn: number; name: string; country?: string | null; handle?: string | null; to?: string; size?: "md" | "lg" }) {
  const label = name || handle || `AS${asn}`;
  const inner = (
    <span className="min-w-0">
      <span className={cx("block truncate font-semibold", size === "lg" ? "text-[17px]" : "text-[14px]")} title={label}>
        {label}
      </span>
      <span className="flex items-center gap-1.5 text-[12.5px] text-muted num">
        AS{asn}
        {handle && handle !== label && <span className="truncate">{handle}</span>}
        {country && <span className="rounded-sm border border-rule-strong px-1 text-[11px] leading-[15px] text-ink-2">{country}</span>}
      </span>
    </span>
  );
  return to ? (
    <Link to={to} className="block min-w-0 hover:[&_span:first-child]:text-accent">
      {inner}
    </Link>
  ) : (
    inner
  );
}

export function LinkDot({ color, className }: { color: string; className?: string }) {
  return <span className={cx("inline-block h-2.5 w-2.5 shrink-0 rounded-[3px]", className)} style={{ background: color }} aria-hidden="true" />;
}

export const th = "px-3 py-2 text-left text-[12.5px] font-medium text-muted";
export const td = "px-3 py-2 align-middle";
