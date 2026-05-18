import { useEffect, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, X } from "lucide-react";
import { DouyinCookies, type DouyinCookie } from "../api/douyin_cookies";

type Props = {
  open: boolean;
  onClose: () => void;
  cookie?: DouyinCookie | null;
};

const MAX_FILE_BYTES = 256 * 1024;

export default function DouyinCookieModal({ open, onClose, cookie }: Props) {
  const qc = useQueryClient();
  const isUpdate = !!cookie;
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setFile(null);
      setName("");
      setError(null);
    }
  }, [open]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { "application/json": [".json"] },
    maxFiles: 1,
    onDrop: (accepted, rejected) => {
      if (rejected.length > 0) {
        setError("Only .json files are accepted.");
        return;
      }
      const f = accepted[0];
      if (!f) return;
      if (f.size > MAX_FILE_BYTES) {
        setError(`File too large (max ${Math.round(MAX_FILE_BYTES / 1024)} KB).`);
        return;
      }
      setError(null);
      setFile(f);
    },
  });

  const mut = useMutation({
    mutationFn: () =>
      isUpdate
        ? DouyinCookies.update(cookie!.id, file!)
        : DouyinCookies.create(file!, name.trim()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["douyin-cookies"] });
      onClose();
    },
    onError: (e: any) => {
      const detail = e?.response?.data?.detail;
      setError(typeof detail === "string" ? detail : `Error: ${e.message}`);
    },
  });

  const submitDisabled =
    !file || (!isUpdate && !name.trim()) || mut.isPending;

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
            {isUpdate ? `Update cookie "${cookie!.name}"` : "Add Douyin Cookie"}
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="close"
          >
            <X size={18} />
          </button>
        </div>

        {!isUpdate && (
          <div className="space-y-1">
            <label className="text-xs uppercase tracking-wide text-slate-500">
              Name
            </label>
            <input
              type="text"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. My Douyin account"
              maxLength={128}
            />
          </div>
        )}

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
              Drag and drop a Cookie-Editor JSON file, or click to select.
            </span>
          )}
        </div>

        {error && (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            disabled={submitDisabled}
            onClick={() => mut.mutate()}
          >
            <Upload size={14} />
            {mut.isPending ? "Saving…" : isUpdate ? "Update" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
