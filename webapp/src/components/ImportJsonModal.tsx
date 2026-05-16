import { useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, X } from "lucide-react";
import { Accounts } from "../api/client";

type Props = {
  open: boolean;
  onClose: () => void;
};

const MAX_FILE_BYTES = 256 * 1024;
const USERNAME_RE = /^[A-Za-z0-9_.]{1,32}$/;

export default function ImportJsonModal({ open, onClose }: Props) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [username, setUsername] = useState("");
  const [replace, setReplace] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setUsername("");
      setReplace(false);
      setError(null);
    }
  }, [open]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { "application/json": [".json"] },
    maxFiles: 1,
    onDrop: (accepted) => {
      const f = accepted[0];
      if (!f) return;
      if (f.size > MAX_FILE_BYTES) {
        setError(`File quá lớn (>${Math.round(MAX_FILE_BYTES / 1024)}KB).`);
        return;
      }
      setError(null);
      setFile(f);
    },
  });

  const mut = useMutation({
    mutationFn: () => Accounts.importJson(file!, username, replace),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      const status = e?.response?.status;
      if (status === 409 && typeof detail === "object" && detail.code === "ACCOUNT_EXISTS") {
        setError(
          "Account đã tồn tại. Bật 'Thay thế' và import lại nếu muốn ghi đè.",
        );
      } else if (status === 403) {
        setError("Yêu cầu bị chặn (Origin invalid). Reload webapp.");
      } else {
        setError(typeof detail === "string" ? detail : `Lỗi: ${e.message}`);
      }
    },
  });

  const submitDisabled =
    !file || !USERNAME_RE.test(username) || mut.isPending;

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
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="close"
          >
            <X size={18} />
          </button>
        </div>

        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded p-4 text-center cursor-pointer text-sm ${
            isDragActive
              ? "border-brand-500 bg-brand-50"
              : "border-slate-300 hover:border-slate-400"
          }`}
        >
          <input {...getInputProps()} />
          {file ? (
            <span className="text-slate-700">
              <Upload size={14} className="inline mr-1" />
              {file.name} ({Math.round(file.size / 1024)} KB)
            </span>
          ) : (
            <span className="text-slate-500">
              Kéo-thả file Cookie-Editor JSON, hoặc click để chọn.
            </span>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-slate-500">
            Username
          </label>
          <input
            type="text"
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="vd: lmbk37"
          />
          <p className="text-xs text-slate-500">
            Chỉ chấp nhận chữ, số, dấu chấm, gạch dưới. Tối đa 32 ký tự.
          </p>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={replace}
            onChange={(e) => setReplace(e.target.checked)}
          />
          Thay thế nếu username đã tồn tại
        </label>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>
            Hủy
          </button>
          <button
            className="btn-primary"
            disabled={submitDisabled}
            onClick={() => mut.mutate()}
          >
            <Upload size={14} />
            {mut.isPending ? "Đang import..." : "Import"}
          </button>
        </div>
      </div>
    </div>
  );
}
