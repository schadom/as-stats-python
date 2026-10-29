const SI = ["", "k", "M", "G", "T", "P"];

function scaled(value: number, base: number, digits: number): [string, string] {
  let v = Math.abs(value);
  let i = 0;
  while (v >= base && i < SI.length - 1) {
    v /= base;
    i++;
  }
  const d = v >= 100 || i === 0 ? 0 : v >= 10 ? Math.min(digits, 1) : digits;
  return [`${value < 0 ? "-" : ""}${v.toFixed(d)}`, SI[i]];
}

let rateSuffix = "b/s";

/** Pick how bit rates are written: "b/s" (12.4 Mb/s) or "bit/s" (12.4 Mbit/s). Set from the site settings. */
export function setRateUnit(unit: string | undefined): void {
  rateSuffix = unit === "bit/s" ? "bit/s" : "b/s";
}

/** bits per second, e.g. "12.4 Gb/s" or "12.4 Gbit/s" */
export function bps(value: number, digits = 1): string {
  const [n, p] = scaled(value, 1000, digits);
  return `${n} ${p}${rateSuffix}`;
}

/** byte volume, e.g. "3.2 TB" */
export function bytes(value: number, digits = 1): string {
  const [n, p] = scaled(value, 1000, digits);
  return `${n} ${p}B`;
}

/** average rate in bits/s of a byte total over a duration */
export function avgBps(totalBytes: number, seconds: number): number {
  return seconds > 0 ? (totalBytes * 8) / seconds : 0;
}

export function pct(part: number, total: number): string {
  if (!total) return "0%";
  const p = (part / total) * 100;
  return p >= 10 ? `${p.toFixed(0)}%` : `${p.toFixed(1)}%`;
}

export function speed(mbit: number | null | undefined): string {
  if (!mbit) return "";
  return mbit >= 1000 ? `${mbit / 1000}G` : `${mbit}M`;
}

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export function ago(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "never";
  const s = Math.round(ts - now);
  const a = Math.abs(s);
  if (a < 60) return rtf.format(s, "second");
  if (a < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (a < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

export function dateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function rangeLabel(start: number, end: number): string {
  return `${dateTime(start)} – ${dateTime(end)}`;
}
