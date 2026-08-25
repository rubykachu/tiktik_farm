import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, FileJson, Edit2, Copy } from "lucide-react";
import { toast } from "sonner";
import { Accounts } from "../api/client";
import { DouyinCookies, type DouyinCookie } from "../api/douyin_cookies";
import ConfirmDialog from "../components/ConfirmDialog";
import { formatDateTime } from "../utils/datetime";
import ImportJsonModal from "../components/ImportJsonModal";
import DouyinCookieModal from "../components/DouyinCookieModal";

type DeleteTarget = { id: number; username: string } | null;
type DeleteCookieTarget = { id: number; name: string } | null;

function CopyCell({ value, mask }: { value?: string | null; mask?: boolean }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="truncate max-w-[120px] font-mono text-xs">{mask ? "••••••••" : value}</span>
      <button
        className="text-slate-400 hover:text-slate-600 flex-shrink-0"
        onClick={() => { navigator.clipboard.writeText(value); toast.success("Đã sao chép"); }}
        title="Sao chép"
      >
        <Copy size={12} />
      </button>
    </span>
  );
}

export default function AccountsPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"tiktok" | "douyin">("tiktok");

  // TikTok tab state
  const { data, isLoading, isError } = useQuery({ queryKey: ["accounts"], queryFn: Accounts.list });
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [jsonModalOpen, setJsonModalOpen] = useState(false);
  const [updateCookieTarget, setUpdateCookieTarget] = useState<{ id: number; username: string; display_name?: string | null } | null>(null);

  // Douyin tab state
  const { data: cookies, isLoading: cookiesLoading, isError: cookiesError } = useQuery({
    queryKey: ["douyin-cookies"],
    queryFn: DouyinCookies.list,
  });
  const [cookieModalOpen, setCookieModalOpen] = useState(false);
  const [editCookie, setEditCookie] = useState<DouyinCookie | null>(null);
  const [deleteCookieTarget, setDeleteCookieTarget] = useState<DeleteCookieTarget>(null);

  const deleteMut = useMutation({
    mutationFn: (id: number) => Accounts.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`Đã xóa tài khoản "${deleteTarget?.username ?? ""}"`);
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  const deleteCookieMut = useMutation({
    mutationFn: (id: number) => DouyinCookies.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["douyin-cookies"] });
      toast.success(`Đã xóa cookie "${deleteCookieTarget?.name ?? ""}"`);
      setDeleteCookieTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail ?? e.message });
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
          <h2 className="text-2xl font-semibold">Tài khoản</h2>
          <p className="text-sm text-slate-500 mt-1">
            Quản lý tài khoản TikTok và tài khoản Douyin.
          </p>
        </div>
        {tab === "tiktok" && (
          <button className="btn-secondary" onClick={() => setJsonModalOpen(true)}>
            <FileJson size={16} />
            Thêm Cookie TikTok
          </button>
        )}
        {tab === "douyin" && (
          <button className="btn-secondary" onClick={() => { setEditCookie(null); setCookieModalOpen(true); }}>
            <FileJson size={16} />
            Thêm Tài khoản Douyin
          </button>
        )}
      </div>

      {/* Tab bar */}
      <div className="flex gap-0 border-b border-slate-200">
        <button className={tabClass(tab === "tiktok")} onClick={() => setTab("tiktok")}>
          Tài khoản TikTok
        </button>
        <button className={tabClass(tab === "douyin")} onClick={() => setTab("douyin")}>
          Tài khoản Douyin
        </button>
      </div>

      {tab === "tiktok" && (
        <>
          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="text-left p-3">Nhãn</th>
                  <th className="text-left p-3">Tài khoản đăng nhập</th>
                  <th className="text-left p-3">Mật khẩu</th>
                  <th className="text-left p-3">Phiên đăng nhập</th>
                  <th className="text-left p-3">Ngày tạo</th>
                  <th className="text-left p-3">Ngày cập nhật</th>
                  <th className="p-3 text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {isLoading && (
                  <tr>
                    <td className="p-4 text-slate-500" colSpan={7}>
                      Đang tải…
                    </td>
                  </tr>
                )}
                {isError && (
                  <tr>
                    <td className="p-4 text-center text-red-400" colSpan={7}>
                      Không thể tải. Kiểm tra kết nối.
                    </td>
                  </tr>
                )}
                {!isLoading && !isError && data && data.length === 0 && (
                  <tr>
                    <td className="p-6 text-center text-slate-500" colSpan={7}>
                      Chưa có tài khoản nào. Nhấn "Thêm Cookie TikTok" để thêm.
                    </td>
                  </tr>
                )}
                {data?.map((a) => (
                  <tr key={a.id} className="hover:bg-slate-50">
                    <td className="p-3 font-medium">{a.display_name || a.username}</td>
                    <td className="p-3"><CopyCell value={a.login} /></td>
                    <td className="p-3"><CopyCell value={a.login_password} mask /></td>
                    <td className="p-3">
                      <span
                        className={
                          "chip " +
                          (a.has_valid_session
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-amber-100 text-amber-700")
                        }
                      >
                        {a.has_valid_session ? "Hợp lệ" : "Cần đăng nhập lại"}
                      </span>
                    </td>
                    <td className="p-3 text-slate-500">{formatDateTime(a.created_at)}</td>
                    <td className="p-3 text-slate-500">{formatDateTime(a.updated_at)}</td>
                    <td className="p-3 text-right space-x-2">
                      <button
                        className="inline-flex items-center gap-1 text-brand-600 text-xs font-medium"
                        onClick={() => setUpdateCookieTarget({ id: a.id, username: a.username, display_name: a.display_name })}
                      >
                        <Edit2 size={14} /> Cập nhật
                      </button>
                      <button
                        className="inline-flex items-center gap-1 text-red-600 text-xs font-medium"
                        onClick={() => setDeleteTarget({ id: a.id, username: a.username })}
                      >
                        <Trash2 size={14} /> Xóa
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
                <th className="text-left p-3">Nhãn</th>
                <th className="text-left p-3">Tài khoản đăng nhập</th>
                <th className="text-left p-3">Mật khẩu</th>
                <th className="text-left p-3">Ngày tạo</th>
                <th className="text-left p-3">Ngày cập nhật</th>
                <th className="p-3 text-right">Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {cookiesLoading && (
                <tr>
                  <td className="p-4 text-slate-500" colSpan={6}>
                    Đang tải…
                  </td>
                </tr>
              )}
              {cookiesError && (
                <tr>
                  <td className="p-4 text-center text-red-400" colSpan={6}>
                    Không thể tải. Kiểm tra kết nối.
                  </td>
                </tr>
              )}
              {!cookiesLoading && !cookiesError && cookies && cookies.length === 0 && (
                <tr>
                  <td className="p-6 text-center text-slate-500" colSpan={6}>
                    Chưa có tài khoản nào. Nhấn "Thêm Tài khoản Douyin" để thêm.
                  </td>
                </tr>
              )}
              {cookies?.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className="p-3 font-medium">{c.name}</td>
                  <td className="p-3"><CopyCell value={c.login} /></td>
                  <td className="p-3"><CopyCell value={c.login_password} mask /></td>
                  <td className="p-3 text-slate-500">{formatDateTime(c.created_at)}</td>
                  <td className="p-3 text-slate-500">{formatDateTime(c.updated_at)}</td>
                  <td className="p-3 text-right space-x-2">
                    <button
                      className="inline-flex items-center gap-1 text-brand-600 text-xs font-medium"
                      onClick={() => { setEditCookie(c); setCookieModalOpen(true); }}
                    >
                      <Edit2 size={14} /> Cập nhật
                    </button>
                    <button
                      className="inline-flex items-center gap-1 text-red-600 text-xs font-medium"
                      onClick={() => setDeleteCookieTarget({ id: c.id, name: c.name })}
                    >
                      <Trash2 size={14} /> Xóa
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ImportJsonModal
        open={jsonModalOpen || updateCookieTarget !== null}
        onClose={() => { setJsonModalOpen(false); setUpdateCookieTarget(null); }}
        updateAccount={updateCookieTarget}
      />

      <DouyinCookieModal
        open={cookieModalOpen}
        onClose={() => { setCookieModalOpen(false); setEditCookie(null); }}
        cookie={editCookie}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Xóa tài khoản "${deleteTarget?.username ?? ""}"?`}
        body={
          <p>
            Tài khoản và file cookie sẽ bị xóa vĩnh viễn. Không thể hoàn tác.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />

      <ConfirmDialog
        open={deleteCookieTarget !== null}
        title={`Xóa cookie "${deleteCookieTarget?.name ?? ""}"?`}
        body={
          <p>
            Các lịch đăng dùng cookie này sẽ không chạy được cho đến khi chọn cookie mới.
            Không thể hoàn tác.
          </p>
        }
        onCancel={() => setDeleteCookieTarget(null)}
        onConfirm={() => deleteCookieTarget && deleteCookieMut.mutate(deleteCookieTarget.id)}
      />
    </div>
  );
}
