import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Accounts } from "../api/client";

type Props = {
  open: boolean;
  onClose: () => void;
  updateAccount?: { id: number; username: string; display_name?: string | null } | null;
};

const USERNAME_RE = /^[A-Za-z0-9_.]{1,32}$/;

export default function ImportJsonModal({ open, onClose, updateAccount }: Props) {
  const qc = useQueryClient();
  const isUpdate = !!updateAccount;
  const [text, setText] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [login, setLogin] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setText("");
      setUsername("");
      setDisplayName("");
      setLogin("");
      setLoginPassword("");
      setError(null);
    } else if (updateAccount) {
      setUsername(updateAccount.username);
      setDisplayName(updateAccount.display_name ?? "");
    }
  }, [open, updateAccount]);

  const mut = useMutation({
    mutationFn: () => {
      if (isUpdate && !text.trim()) {
        // No new cookie — update display_name/login/password via PATCH
        return Accounts.update(updateAccount!.id, {
          display_name: displayName || undefined,
          login: login || undefined,
          login_password: loginPassword || undefined,
        });
      }
      const file = new File([text], "cookie.json", { type: "application/json" });
      return Accounts.importJson(file, username, isUpdate, login || undefined, loginPassword || undefined);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      const status = e?.response?.status;
      if (status === 409 && typeof detail === "object" && detail.code === "ACCOUNT_EXISTS") {
        setError("Tài khoản đã tồn tại. Xóa tài khoản cũ rồi thêm lại.");
      } else if (status === 403) {
        setError("Yêu cầu bị chặn (Origin không hợp lệ). Hãy tải lại trang.");
      } else {
        setError(typeof detail === "string" ? detail : `Error: ${e.message}`);
      }
    },
  });

  function handleSubmit() {
    if (text.trim()) {
      try {
        JSON.parse(text);
      } catch {
        setError("JSON không hợp lệ. Kiểm tra lại và thử lại.");
        return;
      }
    }
    setError(null);
    mut.mutate();
  }

  const submitDisabled =
    (!isUpdate && (!text.trim() || !USERNAME_RE.test(username))) ||
    (isUpdate && !text.trim() && !displayName && !login && !loginPassword) ||
    mut.isPending;

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
          <h3 className="text-lg font-semibold">
            {isUpdate ? `Cập nhật Cookie TikTok "${updateAccount!.username}"` : "Nhập Cookie JSON"}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="close">
            <X size={18} />
          </button>
        </div>

        {!isUpdate && (
          <div className="space-y-1">
            <label className="label-required">Nhãn</label>
            <input
              type="text"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. lmbk37"
            />
          </div>
        )}

        {isUpdate && (
          <div className="space-y-1">
            <label className="label">Nhãn</label>
            <input
              type="text"
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={updateAccount!.username}
              maxLength={128}
            />
          </div>
        )}

        <div className="space-y-1">
          <label className={isUpdate ? "label" : "label-required"}>
            Cookie JSON
            {isUpdate && <span className="text-slate-400 normal-case font-normal"> (để trống để giữ cookie hiện tại)</span>}
          </label>
          <textarea
            className="input min-h-[120px] font-mono text-xs"
            value={text}
            onChange={(e) => { setText(e.target.value); setError(null); }}
            placeholder="Dán Cookie-Editor JSON vào đây…"
          />
        </div>

        <div className="space-y-1">
          <label className="label">Tài khoản đăng nhập</label>
          <input
            type="text"
            className="input"
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            placeholder="Email hoặc username đăng nhập"
            maxLength={256}
          />
        </div>

        <div className="space-y-1">
          <label className="label">Mật khẩu</label>
          <input
            type="password"
            className="input"
            value={loginPassword}
            onChange={(e) => setLoginPassword(e.target.value)}
            placeholder="Mật khẩu tài khoản"
            maxLength={512}
          />
        </div>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>Hủy</button>
          <button className="btn-primary" disabled={submitDisabled} onClick={handleSubmit}>
            {mut.isPending ? "Đang lưu…" : isUpdate ? "Cập nhật" : "Nhập"}
          </button>
        </div>
      </div>
    </div>
  );
}
