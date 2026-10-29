import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { get, type LinkMeta, type Meta, type User } from "./api";

// ---------------------------------------------------------------- session

interface Session {
  user: User;
  meta: Meta;
  isAdmin: boolean;
  linkById: Map<number, LinkMeta>;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ user, meta, children }: { user: User; meta: Meta; children: ReactNode }) {
  const value = useMemo(
    () => ({ user, meta, isAdmin: user.role === "admin", linkById: new Map(meta.links.map((l) => [l.id, l])) }),
    [user, meta],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("no session");
  return s;
}

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: () => get<Meta>("/meta"), staleTime: 60_000 });
}

// ---------------------------------------------------------------- global filters (kept in the URL)

export const RANGES: { id: string; label: string }[] = [
  { id: "6h", label: "6h" },
  { id: "24h", label: "24h" },
  { id: "48h", label: "48h" },
  { id: "7d", label: "7d" },
  { id: "30d", label: "30d" },
  { id: "90d", label: "90d" },
  { id: "365d", label: "1y" },
];

export type IpFilter = "all" | "4" | "6";

export interface Filters {
  range: string;
  ip: IpFilter;
  links: number[];
}

export function useFilters(): [Filters, (patch: Partial<Filters>) => void] {
  const [params, setParams] = useSearchParams();
  const filters = useMemo<Filters>(() => {
    const ip = params.get("ip");
    return {
      range: params.get("range") || "24h",
      ip: ip === "4" || ip === "6" ? ip : "all",
      links: (params.get("links") || "")
        .split(",")
        .filter(Boolean)
        .map(Number)
        .filter((n) => Number.isFinite(n)),
    };
  }, [params]);
  const update = useCallback(
    (patch: Partial<Filters>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (patch.range !== undefined) patch.range === "24h" ? next.delete("range") : next.set("range", patch.range);
          if (patch.ip !== undefined) patch.ip === "all" ? next.delete("ip") : next.set("ip", patch.ip);
          if (patch.links !== undefined) patch.links.length ? next.set("links", patch.links.join(",")) : next.delete("links");
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  return [filters, update];
}

/** Query string carrying the filters, for API calls and for links between pages. */
export function filterQuery(f: Filters, extra: Record<string, string | number | undefined> = {}): string {
  const p = new URLSearchParams();
  p.set("range", f.range);
  if (f.ip !== "all") p.set("ip", f.ip);
  if (f.links.length) p.set("links", f.links.join(","));
  for (const [k, v] of Object.entries(extra)) if (v !== undefined) p.set(k, String(v));
  return p.toString();
}

/** Same, but only the parts worth keeping in a page URL. */
export function navQuery(f: Filters): string {
  const p = new URLSearchParams();
  if (f.range !== "24h") p.set("range", f.range);
  if (f.ip !== "all") p.set("ip", f.ip);
  if (f.links.length) p.set("links", f.links.join(","));
  const s = p.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------- theme

type Theme = "light" | "dark" | "system";

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("asstats-theme") as Theme) || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    apply(theme);
    try {
      localStorage.setItem("asstats-theme", theme);
    } catch {
      /* storage unavailable */
    }
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);
  return [theme, setTheme];
}

/** Re-renders when the theme attribute flips, so charts can pick up new colors. */
export function useIsDark(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === "dark");
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(document.documentElement.dataset.theme === "dark"));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}
