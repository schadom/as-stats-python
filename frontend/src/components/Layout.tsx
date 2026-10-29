import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post, type SearchHit } from "../api";
import { RANGES, navQuery, useFilters, useSession, useTheme } from "../state";
import { cx, LinkDot, Segmented } from "./ui";

function Icon({ d }: { d: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const ICONS = {
  top: "M3 4h14M3 10h9M3 16h5",
  links: "M2 10h16M5 6c2.5-3 4.5-3 7 0s4.5 3 6 0M5 14c2.5 3 4.5 3 7 0s4.5-3 6 0",
  ix: "M4 4h5v5H4zM11 11h5v5h-5zM9 6.5h4.5V11M11 13.5H6.5V9",
  linkcfg: "M8 12l4-4M7 9.5L5.5 11a2.5 2.5 0 103.5 3.5l1.5-1.5M13 10.5L14.5 9A2.5 2.5 0 1011 5.5L9.5 7",
  exporters: "M3 5h14v4H3zM3 11h14v4H3zM6 7h.01M6 13h.01",
  users: "M7 9a3 3 0 100-6 3 3 0 000 6zM2 17c0-3 2.2-5 5-5s5 2 5 5M13.5 4a3 3 0 010 5.5M15 12.2c1.8.6 3 2.4 3 4.8",
  general: "M10 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM10 2v2M10 16v2M3.5 3.5l1.4 1.4M15.1 15.1l1.4 1.4M2 10h2M16 10h2M3.5 16.5l1.4-1.4M15.1 4.9l1.4-1.4",
  status: "M2 10h3l2-5 3 10 2-6 2 3h4",
};

function NavItem({ to, icon, children, end, onNavigate }: { to: string; icon: keyof typeof ICONS; children: ReactNode; end?: boolean; onNavigate?: () => void }) {
  const loc = useLocation();
  // carry the global filters between the stats pages
  const keep = ["/", "/links", "/ix"].includes(to) ? loc.search : "";
  return (
    <NavLink
      to={to + keep}
      end={end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cx(
          "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 font-medium transition-colors",
          isActive ? "bg-surface text-ink shadow-[inset_2px_0_0_var(--c-accent)]" : "text-ink-2 hover:bg-surface/60 hover:text-ink",
        )
      }
    >
      <Icon d={ICONS[icon]} />
      {children}
    </NavLink>
  );
}

function Search() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const loc = useLocation();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 180);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const { data = [] } = useQuery({
    queryKey: ["search", debounced],
    queryFn: () => get<SearchHit[]>(`/search?q=${encodeURIComponent(debounced)}&limit=8`),
    enabled: debounced.length > 0,
  });

  const go = (asn: number) => {
    setOpen(false);
    setQ("");
    navigate(`/as/${asn}${loc.search}`);
  };

  return (
    <div ref={box} className="relative w-full max-w-sm">
      <label className="sr-only" htmlFor="as-search">Find an AS</label>
      <input
        id="as-search"
        value={q}
        placeholder="Find an AS by number or name"
        autoComplete="off"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, data.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Escape") setOpen(false);
          else if (e.key === "Enter") {
            const m = q.trim().match(/^(?:as)?\s*(\d+)$/i);
            if (data[active]) go(data[active].asn);
            else if (m) go(Number(m[1]));
          }
        }}
        className="h-9 w-full rounded-md border border-rule-strong bg-surface pr-3 pl-8 placeholder:text-faint focus:border-accent focus:outline-none"
      />
      <svg className="pointer-events-none absolute top-2.5 left-2.5 text-faint" width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <circle cx="8.5" cy="8.5" r="5.5" />
        <path d="M13 13l4 4" strokeLinecap="round" />
      </svg>
      {open && debounced && (
        <ul className="absolute z-30 mt-1 w-full overflow-hidden rounded-md border border-rule bg-surface shadow-lg" role="listbox">
          {data.length === 0 && <li className="px-3 py-2 text-muted">No AS matches “{debounced}”</li>}
          {data.map((h, i) => (
            <li key={h.asn} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => go(h.asn)}
                className={cx("flex w-full items-baseline gap-2 px-3 py-2 text-left", i === active && "bg-sunken")}
              >
                <span className="w-24 shrink-0 text-muted num">AS{h.asn}</span>
                <span className="truncate">{h.name || h.handle || "Unknown"}</span>
                {h.country && <span className="ml-auto text-[12px] text-muted">{h.country}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function FilterBar({ extra }: { extra?: ReactNode }) {
  const { meta } = useSession();
  const [f, setF] = useFilters();
  const links = meta.links.filter((l) => l.enabled);
  const toggle = (id: number) => {
    const set = new Set(f.links);
    set.has(id) ? set.delete(id) : set.add(id);
    setF({ links: [...set] });
  };
  return (
    <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-2">
      <Segmented label="Time range" value={f.range} options={RANGES} onChange={(range) => setF({ range })} />
      <Segmented
        label="IP version"
        value={f.ip}
        options={[
          { id: "all", label: "IPv4 + IPv6" },
          { id: "4", label: "IPv4" },
          { id: "6", label: "IPv6" },
        ]}
        onChange={(ip) => setF({ ip })}
      />
      {links.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Links">
          {links.map((l) => {
            const on = f.links.length === 0 || f.links.includes(l.id);
            return (
              <button
                key={l.id}
                type="button"
                aria-pressed={f.links.includes(l.id)}
                onClick={() => toggle(l.id)}
                className={cx(
                  "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-[13px] transition-colors",
                  f.links.includes(l.id) ? "border-ink bg-surface font-medium" : "border-rule-strong",
                  on ? "text-ink" : "text-faint",
                )}
                title={f.links.includes(l.id) ? `Stop filtering on ${l.name}` : `Show only ${l.name}`}
              >
                <LinkDot color={l.color} className={on ? "" : "opacity-40"} />
                {l.name}
              </button>
            );
          })}
          {f.links.length > 0 && (
            <button type="button" onClick={() => setF({ links: [] })} className="h-7 px-1.5 text-[13px] text-accent hover:underline">
              All links
            </button>
          )}
        </div>
      )}
      {extra && <div className="ml-auto flex items-center gap-2">{extra}</div>}
    </div>
  );
}

export function Layout() {
  const { user, meta, isAdmin } = useSession();
  const [theme, setTheme] = useTheme();
  const [menu, setMenu] = useState(false);
  const qc = useQueryClient();
  const loc = useLocation();
  const [f] = useFilters();
  useEffect(() => setMenu(false), [loc.pathname]);

  const logout = async () => {
    await post("/auth/logout").catch(() => undefined);
    qc.clear();
    window.location.href = "/";
  };

  const close = () => setMenu(false);
  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      <NavItem to="/" icon="top" end onNavigate={close}>Top AS</NavItem>
      <NavItem to="/links" icon="links" onNavigate={close}>Links</NavItem>
      {(meta.has_ix || meta.my_asn) && <NavItem to="/ix" icon="ix" onNavigate={close}>Internet exchanges</NavItem>}
      <NavItem to="/status" icon="status" onNavigate={close}>Collector status</NavItem>
      {isAdmin && (
        <>
          <div className="mt-5 mb-1 px-2.5 text-[12.5px] font-medium text-muted">Configuration</div>
          <NavItem to="/settings/links" icon="linkcfg" onNavigate={close}>Links & interfaces</NavItem>
          <NavItem to="/settings/exporters" icon="exporters" onNavigate={close}>Exporters</NavItem>
          <NavItem to="/settings/users" icon="users" onNavigate={close}>Users</NavItem>
          <NavItem to="/settings/general" icon="general" onNavigate={close}>General</NavItem>
        </>
      )}
    </nav>
  );

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-40 flex w-[232px] flex-col border-r border-rule bg-canvas px-3 py-4 transition-transform lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
          menu ? "translate-x-0 shadow-2xl" : "-translate-x-full",
        )}
      >
        <NavLink to={`/${navQuery(f)}`} className="mb-6 flex items-center gap-2.5 px-2.5">
          <img src="/favicon.svg" alt="" width="26" height="26" />
          <span className="text-[16px] font-semibold tracking-[-0.01em]">{meta.site_name || "AS-Stats"}</span>
        </NavLink>
        {nav}
        <div className="mt-auto border-t border-rule pt-3">
          <div className="px-2.5 pb-2">
            <div className="truncate font-medium">{user.username}</div>
            <div className="text-[12.5px] text-muted">{user.role === "admin" ? "Administrator" : "Read-only"}</div>
          </div>
          <div className="flex flex-col gap-0.5">
            <NavLink to="/account" className="rounded-md px-2.5 py-1.5 text-ink-2 hover:bg-surface/60 hover:text-ink">Account</NavLink>
            <button
              type="button"
              onClick={() => setTheme(theme === "system" ? "dark" : theme === "dark" ? "light" : "system")}
              className="rounded-md px-2.5 py-1.5 text-left text-ink-2 hover:bg-surface/60 hover:text-ink"
            >
              Theme: {theme === "system" ? "match system" : theme}
            </button>
            <button type="button" onClick={logout} className="rounded-md px-2.5 py-1.5 text-left text-ink-2 hover:bg-surface/60 hover:text-ink">
              Log out
            </button>
          </div>
          <div className="mt-2 px-2.5 text-[12px] text-faint">v{meta.version}</div>
        </div>
      </aside>
      {menu && <div className="fixed inset-0 z-30 bg-[#0b1118]/40 lg:hidden" onClick={close} aria-hidden="true" />}

      <div className="min-w-0">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-rule bg-canvas/90 px-4 backdrop-blur lg:px-8">
          <button type="button" className="rounded-md p-1.5 text-ink-2 hover:bg-sunken lg:hidden" aria-label="Open menu" onClick={() => setMenu(true)}>
            <Icon d="M3 5h14M3 10h14M3 15h14" />
          </button>
          <Search />
        </header>
        <main className="mx-auto max-w-[1360px] px-4 py-6 lg:px-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
