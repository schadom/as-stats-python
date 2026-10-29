import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import "./index.css";
import { ApiError, get, type User } from "./api";
import { Layout } from "./components/Layout";
import { ErrorNote, Loading } from "./components/ui";
import AsDetail from "./pages/AsDetail";
import { Account, ForcePasswordChange, Login } from "./pages/Auth";
import Ix from "./pages/Ix";
import Links from "./pages/Links";
import Status from "./pages/Status";
import TopAS from "./pages/TopAS";
import SettingsExporters from "./pages/settings/SettingsExporters";
import SettingsGeneral from "./pages/settings/SettingsGeneral";
import SettingsLinks from "./pages/settings/SettingsLinks";
import SettingsUsers from "./pages/settings/SettingsUsers";
import { SessionProvider, useMeta, useSession, useTheme } from "./state";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

function AdminOnly({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useSession();
  return isAdmin ? <>{children}</> : <Navigate to="/" replace />;
}

function Authenticated({ user }: { user: User }) {
  const meta = useMeta();
  if (meta.isLoading) return <Loading label="Loading AS-Stats" />;
  if (meta.error || !meta.data) return <div className="p-8"><ErrorNote error={meta.error} /></div>;
  return (
    <SessionProvider user={user} meta={meta.data}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<TopAS />} />
          <Route path="as/:asn" element={<AsDetail />} />
          <Route path="links" element={<Links />} />
          <Route path="ix" element={<Ix />} />
          <Route path="ix/:id" element={<Ix />} />
          <Route path="status" element={<Status />} />
          <Route path="account" element={<Account />} />
          <Route path="settings/links" element={<AdminOnly><SettingsLinks /></AdminOnly>} />
          <Route path="settings/exporters" element={<AdminOnly><SettingsExporters /></AdminOnly>} />
          <Route path="settings/users" element={<AdminOnly><SettingsUsers /></AdminOnly>} />
          <Route path="settings/general" element={<AdminOnly><SettingsGeneral /></AdminOnly>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </SessionProvider>
  );
}

function App() {
  useTheme();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => get<User>("/auth/me"), retry: false, staleTime: Infinity });

  useEffect(() => {
    const onAuth = () => qc.invalidateQueries({ queryKey: ["me"] });
    window.addEventListener("asstats:auth", onAuth);
    return () => window.removeEventListener("asstats:auth", onAuth);
  }, [qc]);

  const setUser = (u: User) => {
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
    qc.setQueryData(["me"], u);
  };

  if (me.isLoading) return <Loading label="Loading AS-Stats" />;
  if (me.error instanceof ApiError && me.error.status === 401) return <Login onLogin={setUser} />;
  if (me.error) return <div className="p-8"><ErrorNote error={me.error} /></div>;
  const user = me.data!;
  if (user.must_change_password) return <ForcePasswordChange user={user} onDone={setUser} />;
  return <Authenticated user={user} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
