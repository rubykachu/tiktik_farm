import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { DouyinCookies, type DouyinCookie } from "../api/douyin_cookies";

type Props = {
  open: boolean;
  onClose: () => void;
  cookie?: DouyinCookie | null;
};

export default function DouyinCookieModal({ open, onClose, cookie }: Props) {
  const qc = useQueryClient();
  const isUpdate = !!cookie;
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setText("");
      setName("");
      setError(null);
    }
  }, [open]);

  const mut = useMutation({
    mutationFn: () => {
      const file = new File([text], "cookie.json", { type: "application/json" });
      return isUpdate
        ? DouyinCookies.update(cookie!.id, file)
        : DouyinCookies.create(file, name.trim());
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["douyin-cookies"] });
      onClose();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : `Error: ${e.message}`);
    },
  });

  function handleSubmit() {
    try {
      JSON.parse(text);
    } catch {
      setError("JSON không hợp lệ. Kiểm tra lại và thử lại.");
      return;
    }
    setError(null);
    mut.mutate();
  }

  const submitDisabled = !text.trim() || (!isUpdate && !name.trim()) || mut.isPending;

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
            {isUpdate ? `Cập nhật cookie "${cookie!.name}"` : "Thêm Tài khoản Douyin"}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="close">
            <X size={18} />
          </button>
        </div>

        {!isUpdate && (
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wide text-slate-500">Tên</label>
            <input
              type="text"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="VD: Tài khoản Douyin của tôi"
              maxLength={128}
            />
          </div>
        )}

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-slate-500">Cookie JSON</label>
          <textarea
            className="input min-h-[120px] font-mono text-xs"
            value={text}
            onChange={(e) => { setText(e.target.value); setError(null); }}
            placeholder="Dán Cookie-Editor JSON vào đây…"
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
            {mut.isPending ? "Đang lưu…" : isUpdate ? "Cập nhật" : "Thêm"}
          </button>
        </div>
      </div>
    </div>
  );
}
