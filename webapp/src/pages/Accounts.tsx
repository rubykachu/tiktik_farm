import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Trash2, RefreshCcw, LogIn, Download, FileJson, Edit2 } from "lucide-react";
import { toast } from "sonner";
import { Accounts } from "../api/client";
import { DouyinCookies, type DouyinCookie } from "../api/douyin_cookies";
import ConfirmDialog from "../components/ConfirmDialog";
import ImportJsonModal from "../components/ImportJsonModal";
import DouyinCookieModal from "../components/DouyinCookieModal";

type DeleteTarget = { id: number; username: string } | null;
type DeleteCookieTarget = { id: number; name: string } | null;

export default function AccountsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"tiktok" | "douyin">("tiktok");

  // TikTok tab state
  const { data, isLoading, isError } = useQuery({ queryKey: ["accounts"], queryFn: Accounts.list });
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [jsonModalOpen, setJsonModalOpen] = useState(false);

  // Douyin tab state
  const { data: cookies, isLoading: cookiesLoading, isError: cookiesError } = useQuery({
    queryKey: ["douyin-cookies"],
    queryFn: DouyinCookies.list,
  });
  const [cookieModalOpen, setCookieModalOpen] = useState(false);
  const [editCookie, setEditCookie] = useState<DouyinCookie | null>(null);
  const [deleteCookieTarget, setDeleteCookieTarget] = useState<DeleteCookieTarget>(null);

  const importMut = useMutation({
    mutationFn: Accounts.importFromDisk,
    onSuccess: (imported) => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      const n = imported?.length ?? 0;
      setImportMsg(
        n === 0
          ? "Nothing to import — every cookie file on disk is already registered."
          : `Imported ${n} account${n === 1 ? "" : "s"}: ${imported.map((a) => a.username).join(", ")}`,
      );
    },
    onError: (e: any) => {
      setImportMsg(`Import failed: ${e?.response?.data?.detail ?? e.message}`);
    },
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => Accounts.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`Account "${deleteTarget?.username ?? ""}" deleted`);
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Delete failed", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  const deleteCookieMut = useMutation({
    mutationFn: (id: number) => DouyinCookies.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["douyin-cookies"] });
      toast.success(`Cookie "${deleteCookieTarget?.name ?? ""}" deleted`);
      setDeleteCookieTarget(null);
    },
    onError: (e: any) => {
      toast.error("Delete failed", { description: e?.response?.data?.detail ?? e.message });
      setDeleteCookieTarget(null);
    },
  });

  const tabClass = (active: boolean) =>
    `px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
      active
        ? "border-brand-600 text-brand-600"
        : "border-transparent text-slate-500 hover:text-slate-700"
    }`;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Accounts</h2>
          <p className="text-sm text-slate-500 mt-1">
            TikTok accounts and Douyin cookies.
          </p>
        </div>
        {tab === "tiktok" && (
          <div className="flex gap-2">
            <button
              className="btn-secondary"
              onClick={() => importMut.mutate()}
              disabled={importMut.isPending}
            >
              <Download size={16} />
              Import from disk
            </button>
            <button
              className="btn-secondary"
              onClick={() => setJsonModalOpen(true)}
            >
              <FileJson size={16} />
              Import JSON
            </button>
            <Link to="/login" className="btn-primary">
              <LogIn size={16} />
              Add via browser
            </Link>
          </div>
        )}
        {tab === "douyin" && (
          <button className="btn-primary" onClick={() => { setEditCookie(null); setCookieModalOpen(true); }}>
            Add Cookie
          </button>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex gap-0 border-b border-slate-200">
        <button className={tabClass(tab === "tiktok")} onClick={() => setTab("tiktok")}>
          TikTok Accounts
        </button>
        <button className={tabClass(tab === "douyin")} onClick={() => setTab("douyin")}>
          Douyin Cookies
        </button>
      </div>

      {tab === "tiktok" && (
        <>
          {importMsg && (
            <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 flex items-start justify-between gap-3">
              <span>{importMsg}</span>
              <button
                className="text-slate-400 hover:text-slate-600"
                onClick={() => setImportMsg(null)}
                aria-label="dismiss"
              >
                ×
              </button>
            </div>
          )}

          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="text-left p-3">Username</th>
                  <th className="text-left p-3">Display name</th>
                  <th className="text-left p-3">Session</th>
                  <th className="text-left p-3">Last used</th>
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading && (
                  <tr>
                    <td className="p-4 text-slate-500" colSpan={5}>
                      Loading…
                    </td>
                  </tr>
                )}
                {isError && (
                  <tr>
                    <td className="p-4 text-center text-red-400" colSpan={5}>
                      Failed to load. Check API connection.
                    </td>
                  </tr>
                )}
                {!isLoading && !isError && data && data.length === 0 && (
                  <tr>
                    <td className="p-6 text-center text-slate-500" colSpan={5}>
                      No accounts yet. Use "Add via browser" or "Import from disk".
                    </td>
                  </tr>
                )}
                {data?.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="p-3 font-medium">{a.username}</td>
                    <td className="p-3 text-slate-600">{a.display_name ?? "—"}</td>
                    <td className="p-3">
                      <span
                        className={
                          "chip " +
                          (a.has_valid_session
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-amber-100 text-amber-700")
                        }
                      >
                        {a.has_valid_session ? "valid" : "needs re-login"}
                      </span>
                    </td>
                    <td className="p-3 text-slate-500">
                      {a.last_used_at ? new Date(a.last_used_at).toLocaleString() : "—"}
                    </td>
                    <td className="p-3 text-right space-x-2">
                      <Link
                        to="/login"
                        className="inline-flex items-center gap-1 text-brand-600 text-xs font-medium"
                      >
                        <RefreshCcw size={14} /> Re-login
                      </Link>
                      <button
                        className="inline-flex items-center gap-1 text-red-600 text-xs font-medium"
                        onClick={() => setDeleteTarget({ id: a.id, username: a.username })}
                      >
                        <Trash2 size={14} /> Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "douyin" && (
        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="text-left p-3">Name</th>
                <th className="text-left p-3">Created</th>
                <th className="p-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {cookiesLoading && (
                <tr>
                  <td className="p-4 text-slate-500" colSpan={3}>
                    Loading…
                  </td>
                </tr>
              )}
              {cookiesError && (
                <tr>
                  <td className="p-4 text-center text-red-400" colSpan={3}>
                    Failed to load. Check API connection.
                  </td>
                </tr>
              )}
              {!cookiesLoading && !cookiesError && cookies && cookies.length === 0 && (
                <tr>
                  <td className="p-6 text-center text-slate-500" colSpan={3}>
                    No cookies yet. Click "Add Cookie" to import a Cookie-Editor JSON file.
                  </td>
                </tr>
              )}
              {cookies?.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className="p-3 font-medium">{c.name}</td>
                  <td className="p-3 text-slate-500">
                    {new Date(c.created_at).toLocaleString()}
                  </td>
                  <td className="p-3 text-right space-x-2">
                    <button
                      className="inline-flex items-center gap-1 text-brand-600 text-xs font-medium"
                      onClick={() => { setEditCookie(c); setCookieModalOpen(true); }}
                    >
                      <Edit2 size={14} /> Update
                    </button>
                    <button
                      className="inline-flex items-center gap-1 text-red-600 text-xs font-medium"
                      onClick={() => setDeleteCookieTarget({ id: c.id, name: c.name })}
                    >
                      <Trash2 size={14} /> Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ImportJsonModal
        open={jsonModalOpen}
        onClose={() => setJsonModalOpen(false)}
      />

      <DouyinCookieModal
        open={cookieModalOpen}
        onClose={() => { setCookieModalOpen(false); setEditCookie(null); }}
        cookie={editCookie}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Delete account "${deleteTarget?.username ?? ""}"?`}
        body={
          <p>
            The account and its cookie file will be removed. This action cannot be undone.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />

      <ConfirmDialog
        open={deleteCookieTarget !== null}
        title={`Delete cookie "${deleteCookieTarget?.name ?? ""}"?`}
        body={
          <p>
            Pipelines using this cookie will not run until a new cookie is selected.
            This action cannot be undone.
          </p>
        }
        onCancel={() => setDeleteCookieTarget(null)}
        onConfirm={() => deleteCookieTarget && deleteCookieMut.mutate(deleteCookieTarget.id)}
      />
    </div>
  );
}
