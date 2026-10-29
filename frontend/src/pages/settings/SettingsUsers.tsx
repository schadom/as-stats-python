import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post, type Role, type User } from "../../api";
import { Badge, Button, ErrorNote, Field, Input, Loading, Modal, PageHeader, Panel, Select, Toggle, td, th } from "../../components/ui";
import { ago } from "../../format";
import { useSession } from "../../state";

function UserModal({ user, onClose }: { user: User | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState(user?.username ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "readonly");
  const [active, setActive] = useState(user?.is_active ?? true);
  const [password, setPassword] = useState("");
  const [mustChange, setMustChange] = useState(true);
  const save = useMutation({
    mutationFn: () =>
      user
        ? patch(`/users/${user.id}`, { role, is_active: active, ...(password ? { password, must_change_password: mustChange } : {}) })
        : post("/users", { username: username.trim(), password, role, must_change_password: mustChange }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={user ? `Edit ${user.username}` : "Add user"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || (!user && (!username.trim() || password.length < 8))}>
            {user ? "Save user" : "Add user"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {!user && (
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus autoComplete="off" />
          </Field>
        )}
        <Field label="Role" hint="Read-only users can view all statistics. Administrators can also change links, exporters, users and settings.">
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="readonly">Read-only</option>
            <option value="admin">Administrator</option>
          </Select>
        </Field>
        <Field label={user ? "New password" : "Password"} hint={user ? "Leave empty to keep the current password." : "At least 8 characters."}>
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </Field>
        {(!user || password) && <Toggle checked={mustChange} onChange={setMustChange} label="Ask for a new password at next login" />}
        {user && <Toggle checked={active} onChange={setActive} label="Account can log in" />}
      </div>
      {save.error && <div className="mt-4"><ErrorNote error={save.error} /></div>}
    </Modal>
  );
}

export default function SettingsUsers() {
  const { user: me } = useSession();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/users") });
  const [editing, setEditing] = useState<User | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<User | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => del(`/users/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      setDeleting(null);
    },
  });
  if (users.isLoading) return <Loading />;
  return (
    <>
      <PageHeader title="Users" subtitle="Nobody can see anything without logging in." actions={<Button variant="primary" onClick={() => setEditing(null)}>Add user</Button>} />
      {users.error && <ErrorNote error={users.error} />}
      <Panel flush>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead className="border-b border-rule">
              <tr>
                <th className={th}>User</th>
                <th className={th}>Role</th>
                <th className={th}>Last login</th>
                <th className={th}><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.data?.map((u) => (
                <tr key={u.id} className="border-b border-rule last:border-b-0">
                  <td className={td}>
                    <span className="font-medium">{u.username}</span>
                    {u.id === me.id && <span className="ml-2 text-muted">(you)</span>}
                    <span className="ml-2 inline-flex gap-1.5">
                      {!u.is_active && <Badge tone="danger">Disabled</Badge>}
                      {u.must_change_password && <Badge tone="warn">Must set password</Badge>}
                    </span>
                  </td>
                  <td className={td}>{u.role === "admin" ? "Administrator" : "Read-only"}</td>
                  <td className={`${td} text-muted`}>{ago(u.last_login)}</td>
                  <td className={`${td} text-right whitespace-nowrap`}>
                    <Button size="sm" onClick={() => setEditing(u)}>Edit</Button>
                    {u.id !== me.id && (
                      <Button size="sm" variant="danger" className="ml-2" onClick={() => setDeleting(u)}>Delete</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {editing !== undefined && <UserModal user={editing} onClose={() => setEditing(undefined)} />}
      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.username ?? ""}?`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>Keep user</Button>
            <Button variant="danger" onClick={() => deleting && remove.mutate(deleting.id)} disabled={remove.isPending}>Delete user</Button>
          </>
        }
      >
        <p>The account is removed and logged out everywhere.</p>
        {remove.error && <div className="mt-3"><ErrorNote error={remove.error} /></div>}
      </Modal>
    </>
  );
}
