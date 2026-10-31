import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { post } from "../../api";
import { Button, ErrorNote, Field, Input, Modal } from "../../components/ui";

const WORD = "DELETE";

/** Irreversible actions, kept apart from the normal settings. */
export function DangerZone() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [done, setDone] = useState<number | null>(null);
  const reset = useMutation({
    mutationFn: () => post<{ deleted: number }>("/statistics/reset", { confirm: typed }),
    onSuccess: (r) => {
      setOpen(false);
      setTyped("");
      setDone(r.deleted);
      qc.invalidateQueries();
    },
  });
  const close = () => {
    setOpen(false);
    setTyped("");
    reset.reset();
  };

  return (
    <section className="mt-8 rounded-lg border border-danger/50 bg-surface" aria-labelledby="danger-zone">
      <header className="border-b border-danger/30 px-4 py-2.5">
        <h2 id="danger-zone" className="text-[15px] font-semibold text-danger">Danger zone</h2>
      </header>
      <div className="flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="min-w-0 max-w-2xl">
          <div className="font-medium">Reset all statistics</div>
          <p className="mt-0.5 text-[13px] text-muted">
            Deletes all collected traffic data in every resolution, for all ASes and links. Exporters, links, users, settings and AS names are kept.
            This cannot be undone.
          </p>
          {done !== null && <p className="mt-1.5 text-[13px] text-ok">All statistics deleted ({done.toLocaleString()} rows). New data appears with the next flows.</p>}
        </div>
        <Button variant="danger" onClick={() => { setDone(null); setOpen(true); }}>Reset statistics</Button>
      </div>

      <Modal
        open={open}
        onClose={close}
        title="Reset all statistics?"
        footer={
          <>
            <Button variant="ghost" onClick={close}>Cancel</Button>
            <Button
              variant="danger"
              onClick={() => reset.mutate()}
              disabled={typed !== WORD || reset.isPending}
            >
              {reset.isPending ? "Deleting…" : "Delete all statistics"}
            </Button>
          </>
        }
      >
        <div className="grid gap-3">
          <p>This permanently deletes:</p>
          <ul className="ml-5 list-disc text-ink-2">
            <li>all traffic per AS and per link, at 5 minute, hourly and daily resolution</li>
            <li>prefix statistics, if collected</li>
          </ul>
          <p className="text-ink-2">
            Kept: exporters, links and interfaces, users, settings, AS names and PeeringDB data. The collector keeps running, so new traffic shows up again within a few minutes.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (typed === WORD && !reset.isPending) reset.mutate();
            }}
          >
            <Field label={`Type ${WORD} to confirm`}>
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} placeholder={WORD} autoFocus />
            </Field>
          </form>
          {reset.error && <ErrorNote error={reset.error} />}
        </div>
      </Modal>
    </section>
  );
}
