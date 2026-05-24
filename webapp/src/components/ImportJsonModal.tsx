import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Accounts } from "../api/client";

type Props = {
  open: boolean;
  onClose: () => void;
};

const USERNAME_RE = /^[A-Za-z0-9_.]{1,32}$/;

export default function ImportJsonModal({ open, onClose }: Props) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [username, setUsername] = useState("");
  const [replace, setReplace] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setText("");
      setUsername("");
      setReplace(false);
      setError(null);
    }
  }, [open]);

  const mut = useMutation({
    mutationFn: () => {
      const file = new File([text], "cookie.json", { type: "application/json" });
      return Accounts.importJson(file, username, replace);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      const status = e?.response?.status;
      if (status === 409 && typeof detail === "object" && detail.code === "ACCOUNT_EXISTS") {
        setError("Account already exists. Tick 'Replace' and import again to overwrite.");
      } else if (status === 403) {
        setError("Request blocked (invalid Origin). Reload the webapp.");
      } else {
        setError(typeof detail === "string" ? detail : `Error: ${e.message}`);
      }
    },
  });

  function handleSubmit() {
    try {
      JSON.parse(text);
    } catch {
      setError("Invalid JSON. Check your paste and try again.");
      return;
    }
    setError(null);
    mut.mutate();
  }

  const submitDisabled = !text.trim() || !USERNAME_RE.test(username) || mut.isPending;

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="rounded-lg bg-white shadow-xl w-full max-w-md p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Import JSON Cookie</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="close">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-slate-500">Cookie JSON</label>
          <textarea
            className="input min-h-[120px] font-mono text-xs"
            value={text}
            onChange={(e) => { setText(e.target.value); setError(null); }}
            placeholder="Paste Cookie-Editor JSON here…"
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-slate-500">Username</label>
          <input
            type="text"
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="e.g. lmbk37"
          />
          <p className="text-xs text-slate-500">
            Letters, numbers, dots, underscores only. Up to 32 characters.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
          Replace if the username already exists
        </label>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={submitDisabled} onClick={handleSubmit}>
            {mut.isPending ? "Importing…" : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}
