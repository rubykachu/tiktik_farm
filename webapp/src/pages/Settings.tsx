import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Star, Trash2, Edit, X, ExternalLink, HelpCircle } from "lucide-react";
import { toast } from "sonner";
import { Settings as SettingsApi } from "../api/settings";
import { LLMModels, LLMModel, LLMModelCreate } from "../api/llmModels";
import { TelegramConfigs, TelegramConfig } from "../api/telegram_configs";
import { Telegram, TelegramChatItem } from "../api/telegram";
import ConfirmDialog from "../components/ConfirmDialog";
import Tooltip from "../components/Tooltip";
import { useTelegramChatName } from "../hooks/useTelegramChatName";

type EditState =
  | { mode: "create" }
  | { mode: "edit"; model: LLMModel }
  | null;

export default function SettingsPage() {
  const qc = useQueryClient();

  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: SettingsApi.get,
  });
  const { data: models, isLoading, isError } = useQuery({
    queryKey: ["llm-models"],
    queryFn: LLMModels.list,
  });

  const [editState, setEditState] = useState<EditState>(null);
  const [deleteTarget, setDeleteTarget] = useState<LLMModel | null>(null);

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) => SettingsApi.patch({ llm_enabled: enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  const modeMut = useMutation({
    mutationFn: (mode: "cloud" | "offline") => SettingsApi.patch({ llm_mode: mode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings"] }),
  });

  const setDefaultMut = useMutation({
    mutationFn: (id: number) => LLMModels.setDefault(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["llm-models"] }),
  });

  const deleteMut = useMutation({
    mutationFn: (id: number) => LLMModels.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-models"] });
      toast.success(`Đã xóa model "${deleteTarget?.label ?? ""}"`);
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Cài đặt</h2>
        <p className="text-sm text-slate-500 mt-1">
          Cấu hình chung cho hệ thống và AI dùng để viết lại tiêu đề.
        </p>
      </div>

      <section className="card p-6 space-y-4">
        <header className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-medium">Tự động viết lại tiêu đề bằng AI</h3>
            <p className="text-sm text-slate-500">
              Khi bật, các lịch đăng dùng{" "}
              <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> sẽ gọi
              model mặc định bên dưới để viết lại tiêu đề YouTube.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={settings?.llm_enabled ?? false}
              onChange={(e) => toggleMut.mutate(e.target.checked)}
              disabled={toggleMut.isPending}
            />
            <span className="font-medium">
              {settings?.llm_enabled ? "Đang bật" : "Đang tắt"}
            </span>
          </label>
        </header>

        <div className="flex items-center gap-3 border-t pt-4">
          <span className="text-sm font-medium text-slate-600">Nhà cung cấp:</span>
          <div className="flex rounded-md border border-slate-200 overflow-hidden text-sm">
            <button
              className={`px-3 py-1.5 transition-colors ${
                (settings?.llm_mode ?? "cloud") === "cloud"
                  ? "bg-brand-600 text-white"
                  : "bg-white text-slate-600 hover:bg-slate-50"
              }`}
              onClick={() => modeMut.mutate("cloud")}
              disabled={modeMut.isPending}
            >
              Đám mây
            </button>
            <button
              className={`px-3 py-1.5 border-l border-slate-200 transition-colors ${
                settings?.llm_mode === "offline"
                  ? "bg-brand-600 text-white"
                  : "bg-white text-slate-600 hover:bg-slate-50"
              }`}
              onClick={() => modeMut.mutate("offline")}
              disabled={modeMut.isPending}
            >
              Máy cục bộ (Ollama)
            </button>
          </div>
        </div>

        {settings?.llm_mode === "offline" ? (
          <OllamaPanel
            model={settings.ollama_model}
            baseUrl={settings.ollama_base_url}
          />
        ) : (
          <>
            <div className="flex items-center justify-between border-t pt-4">
              <p className="text-sm text-slate-600">Model đang có</p>
              <button
                className="btn-primary"
                onClick={() => setEditState({ mode: "create" })}
              >
                <Plus size={16} /> Thêm model
              </button>
            </div>

            <div className="card p-0 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="text-left p-3">Mặc định</th>
                    <th className="text-left p-3">Tên hiển thị</th>
                    <th className="text-left p-3">Model ID</th>
                    <th className="text-left p-3">Endpoint</th>
                    <th className="text-left p-3">API key</th>
                    <th className="p-3 text-right">Thao tác</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {isLoading && (
                    <tr>
                      <td className="p-4 text-slate-500" colSpan={6}>Đang tải…</td>
                    </tr>
                  )}
                  {isError && (
                    <tr>
                      <td className="p-4 text-center text-red-400" colSpan={6}>
                        Không thể tải. Kiểm tra kết nối.
                      </td>
                    </tr>
                  )}
                  {!isLoading && !isError && models && models.length === 0 && (
                    <tr>
                      <td className="p-6 text-center text-slate-500" colSpan={6}>
                        Chưa có model nào. Thêm model để bật tính năng viết lại tiêu đề.
                      </td>
                    </tr>
                  )}
                  {models?.map((m) => (
                    <tr key={m.id}>
                      <td className="p-3">
                        {m.is_default ? (
                          <span className="chip bg-emerald-100 text-emerald-700">
                            <Star size={12} className="inline mr-1" /> mặc định
                          </span>
                        ) : (
                          <button
                            className="text-xs text-brand-600 hover:underline"
                            onClick={() => setDefaultMut.mutate(m.id)}
                          >
                            Đặt mặc định
                          </button>
                        )}
                      </td>
                      <td className="p-3 font-medium">{m.label}</td>
                      <td className="p-3 text-slate-600">
                        <code className="text-xs bg-slate-100 px-1 rounded">
                          {m.model_id}
                        </code>
                      </td>
                      <td className="p-3 text-slate-500">
                        {m.api_base ? (
                          <code className="text-xs bg-slate-100 px-1 rounded break-all">
                            {m.api_base}
                          </code>
                        ) : (
                          <span className="text-xs text-slate-400">—</span>
                        )}
                      </td>
                      <td className="p-3 text-slate-500">
                        <code className="text-xs">{m.api_key_masked}</code>
                      </td>
                      <td className="p-3 text-right space-x-2">
                        <button
                          className="text-xs text-slate-600 hover:text-slate-900"
                          onClick={() => setEditState({ mode: "edit", model: m })}
                        >
                          <Edit size={14} className="inline" /> Sửa
                        </button>
                        <button
                          className="text-xs text-red-600"
                          onClick={() => setDeleteTarget(m)}
                        >
                          <Trash2 size={14} className="inline" /> Xóa
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <TelegramConfigsSection />

      {editState && (
        <LLMModelModal
          key={editState.mode === "edit" ? `edit-${editState.model.id}` : "create"}
          state={editState}
          onClose={() => setEditState(null)}
        />
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Xóa "${deleteTarget?.label ?? ""}"?`}
        body={
          <p>
            Model sẽ bị xóa khỏi danh sách. Các lịch đăng đang dùng{" "}
            <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> sẽ dùng
            tiêu đề YouTube gốc cho đến khi có model mặc định mới.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </div>
  );
}

// ─── Ollama local panel ─────────────────────────────────────────────────────

function OllamaPanel({
  model,
  baseUrl,
}: {
  model: string | null;
  baseUrl: string | null;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 space-y-3">
      <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 text-sm">
        <span className="font-medium text-slate-600">Model</span>
        {model ? (
          <code className="bg-white border border-slate-200 px-2 py-0.5 rounded text-xs">
            {model}
          </code>
        ) : (
          <span className="text-xs text-amber-600 italic">chưa cấu hình (OLLAMA_MODEL)</span>
        )}
        <span className="font-medium text-slate-600">Base URL</span>
        {baseUrl ? (
          <code className="bg-white border border-slate-200 px-2 py-0.5 rounded text-xs break-all">
            {baseUrl}
          </code>
        ) : (
          <span className="text-xs text-amber-600 italic">chưa cấu hình (OLLAMA_BASE_URL)</span>
        )}
      </div>
      <p className="text-xs text-slate-500">
        Cấu hình qua biến môi trường{" "}
        <code className="bg-white border border-slate-200 px-1 rounded">OLLAMA_MODEL</code>{" "}
        và{" "}
        <code className="bg-white border border-slate-200 px-1 rounded">OLLAMA_BASE_URL</code>
        . Cần khởi động lại sau khi thay đổi.
      </p>
      {model && (
        <p className="text-xs text-slate-500">
          Cài model:{" "}
          <code className="bg-white border border-slate-200 px-1 rounded">
            ollama pull {model}
          </code>
        </p>
      )}
    </div>
  );
}

// ─── Add / Edit model modal ─────────────────────────────────────────────────
// Parent forces a fresh mount per target via the `key` prop, so component
// state initializers are enough to reset between open/close transitions.

function LLMModelModal({
  state,
  onClose,
}: {
  state: EditState;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = state?.mode === "edit";
  const existing = isEdit ? state.model : null;

  const [label, setLabel] = useState(existing?.label ?? "");
  const [modelId, setModelId] = useState(existing?.model_id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState(existing?.api_base ?? "");
  const [apiVersion, setApiVersion] = useState(existing?.api_version ?? "");
  const [setDefault, setSetDefault] = useState(!isEdit);
  // Single feedback channel for both Test and Save. Whichever action
  // ran most recently owns the chip; switching between them clears the
  // previous result so the user never sees two stacked messages.
  const [notice, setNotice] = useState<
    { ok: boolean; message: string } | null
  >(null);

  const needsEndpoint = modelId.trim().toLowerCase().startsWith("azure/");

  function formatApiError(e: any): string {
    const detail = e?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    // FastAPI validation errors arrive as [{loc, msg, type}, ...]
    if (Array.isArray(detail)) {
      return detail
        .map((d) => `${(d.loc ?? []).join(".")}: ${d.msg}`)
        .join("; ");
    }
    return e?.message || "Request failed";
  }

  const createMut = useMutation({
    mutationFn: (payload: LLMModelCreate) => LLMModels.create(payload),
    onMutate: () => setNotice(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-models"] });
      onClose();
    },
    onError: (e: any) => setNotice({ ok: false, message: formatApiError(e) }),
  });

  const updateMut = useMutation({
    mutationFn: (payload: {
      id: number;
      label: string;
      model_id: string;
      api_key: string;
      api_base: string;
      api_version: string;
    }) =>
      LLMModels.update(payload.id, {
        label: payload.label,
        model_id: payload.model_id,
        api_key: payload.api_key || undefined,
        // Send each endpoint field independently. "" clears on the server
        // (router maps `"" or None` → None) so the user can wipe one field
        // without losing the other. clear_api_base is reserved for callers
        // that want to nuke both atomically.
        api_base: payload.api_base,
        api_version: payload.api_version,
      }),
    onMutate: () => setNotice(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-models"] });
      onClose();
    },
    onError: (e: any) => setNotice({ ok: false, message: formatApiError(e) }),
  });

  const testMut = useMutation({
    mutationFn: () =>
      SettingsApi.testLLM(
        modelId,
        apiKey,
        apiBase || undefined,
        apiVersion || undefined,
      ),
    onMutate: () => setNotice(null),
    onSuccess: (r) => setNotice({ ok: r.ok, message: r.message }),
    onError: (e: any) => setNotice({ ok: false, message: formatApiError(e) }),
  });

  if (!state) return null;

  const busy = createMut.isPending || updateMut.isPending;
  const azureReady =
    !needsEndpoint || (apiBase.trim() && apiVersion.trim());
  const canSubmit =
    label.trim() && modelId.trim() && (isEdit || apiKey) && azureReady;

  // Bind an <input> to a setter and clear any stale Test/Save chip.
  function bind(setter: (v: string) => void) {
    return (e: React.ChangeEvent<HTMLInputElement>) => {
      setter(e.target.value);
      if (notice) setNotice(null);
    };
  }

  function onSave() {
    if (!canSubmit) return;
    if (isEdit && existing) {
      updateMut.mutate({
        id: existing.id,
        label: label.trim(),
        model_id: modelId.trim(),
        api_key: apiKey,
        api_base: apiBase.trim(),
        api_version: apiVersion.trim(),
      });
    } else {
      createMut.mutate({
        label: label.trim(),
        model_id: modelId.trim(),
        api_key: apiKey,
        api_base: apiBase.trim() || undefined,
        api_version: apiVersion.trim() || undefined,
        set_default: setDefault,
      });
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="rounded-lg bg-white shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[calc(100vh-2rem)] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">
            {isEdit ? "Sửa model AI" : "Thêm model AI"}
          </h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
          >
            <X size={20} />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="label-required">Tên hiển thị</label>
            <input
              className="input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Gemini Flash Lite (cheap)"
            />
          </div>
          <div>
            <label className="label-required">Model ID</label>
            <input
              className="input"
              value={modelId}
              onChange={bind(setModelId)}
              placeholder="gemini/gemini-2.0-flash-lite"
            />
            <p className="text-xs text-slate-500 mt-1">
              Định dạng: <code>provider/model</code>. Ví dụ:{" "}
              <code>gemini/gemini-2.0-flash-lite</code>,{" "}
              <code>openai/gpt-4o-mini</code>,{" "}
              <code>azure/my-gpt4o-deployment</code>.{" "}
              <a
                className="text-brand-600 hover:underline inline-flex items-center gap-0.5"
                href="https://docs.litellm.ai/docs/providers"
                target="_blank"
                rel="noopener noreferrer"
              >
                Danh sách đầy đủ <ExternalLink size={11} />
              </a>
            </p>
          </div>
          <div>
            <label className={isEdit ? "label" : "label-required"}>API key</label>
            <input
              type="password"
              className="input"
              value={apiKey}
              onChange={bind(setApiKey)}
              placeholder={
                isEdit ? "Để trống để giữ key hiện tại" : "sk-… or AIza…"
              }
            />
            {isEdit && (
              <p className="text-xs text-slate-500 mt-1">
                Key hiện tại: <code>{existing!.api_key_masked}</code>
              </p>
            )}
          </div>

          {(needsEndpoint || apiBase || apiVersion) && (
            <div className="space-y-3 rounded-md border border-blue-200 bg-blue-50/50 p-3">
              <p className="text-xs font-medium text-blue-700">
                {needsEndpoint
                  ? "Azure endpoint (bắt buộc cho model azure/)"
                  : "Endpoint tùy chỉnh (không bắt buộc)"}
              </p>
              <div>
                <label className={needsEndpoint ? "label-required" : "label"}>API base URL</label>
                <input
                  className="input"
                  value={apiBase}
                  onChange={bind(setApiBase)}
                  placeholder="https://your-resource.openai.azure.com/"
                />
                {needsEndpoint && (
                  <p className="text-xs text-amber-600 mt-1">
                    Chỉ nhập URL gốc — KHÔNG thêm <code>/openai/v1</code> (litellm
                    sẽ tự thêm).
                  </p>
                )}
              </div>
              <div>
                <label className={needsEndpoint ? "label-required" : "label"}>API version</label>
                <input
                  className="input"
                  value={apiVersion}
                  onChange={bind(setApiVersion)}
                  placeholder="2025-02-01-preview"
                />
                <p className="text-xs text-slate-500 mt-1">
                  Để trống dùng mặc định của litellm. Giá trị thông dụng:{" "}
                  <code>2025-04-01-preview</code>, <code>2025-02-01-preview</code>,{" "}
                  <code>2024-08-01-preview</code>.
                </p>
              </div>
            </div>
          )}

          <button
            type="button"
            className="btn-secondary"
            disabled={
              !modelId.trim() ||
              !apiKey ||
              testMut.isPending ||
              !azureReady
            }
            onClick={() => testMut.mutate()}
          >
            {testMut.isPending ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
          </button>

          {!isEdit && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={setDefault}
                onChange={(e) => setSetDefault(e.target.checked)}
              />
              Đặt làm mặc định
            </label>
          )}

          {notice && (
            <div
              className={
                "text-xs break-words rounded-md px-3 py-2 max-h-32 overflow-y-auto " +
                (notice.ok
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : "bg-red-50 text-red-700 border border-red-200")
              }
            >
              <span className="font-medium">
                {notice.ok ? "✓ " : "✗ "}
              </span>
              {notice.message}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-3 border-t">
          <button className="btn-secondary" onClick={onClose}>
            Hủy
          </button>
          <button
            className="btn-primary"
            disabled={!canSubmit || busy}
            onClick={onSave}
          >
            {isEdit ? "Lưu" : "Thêm"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Telegram configs ───────────────────────────────────────────────────────

function maskBotToken(token: string): string {
  return `${token.slice(0, 6)}••••••`;
}

function TelegramConfigsSection() {
  const qc = useQueryClient();
  const { data: configs, isLoading, isError } = useQuery({
    queryKey: ["telegram-configs"],
    queryFn: TelegramConfigs.list,
  });

  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<TelegramConfig | null>(null);

  const deleteMut = useMutation({
    mutationFn: (id: number) => TelegramConfigs.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["telegram-configs"] });
      toast.success(`Đã xóa cấu hình "${deleteTarget?.name ?? ""}"`);
      setDeleteTarget(null);
    },
    onError: (e: any) => {
      toast.error("Xóa thất bại", { description: e?.response?.data?.detail ?? e.message });
      setDeleteTarget(null);
    },
  });

  return (
    <section className="card p-6 space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-medium">Telegram</h3>
          <p className="text-sm text-slate-500">
            Lưu sẵn bot_token/chat_id để chọn nhanh khi tạo lịch đăng, không cần nhập lại mỗi lần.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setAddOpen(true)}>
          <Plus size={16} /> Thêm cấu hình
        </button>
      </header>

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="text-left p-3">Tên</th>
              <th className="text-left p-3">Chat ID</th>
              <th className="text-left p-3">Bot Token</th>
              <th className="p-3 text-right">Thao tác</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td className="p-4 text-slate-500" colSpan={4}>Đang tải…</td>
              </tr>
            )}
            {isError && (
              <tr>
                <td className="p-4 text-center text-red-400" colSpan={4}>
                  Không thể tải. Kiểm tra kết nối.
                </td>
              </tr>
            )}
            {!isLoading && !isError && configs && configs.length === 0 && (
              <tr>
                <td className="p-6 text-center text-slate-500" colSpan={4}>
                  Chưa có cấu hình Telegram nào.
                </td>
              </tr>
            )}
            {configs?.map((c) => (
              <TelegramConfigRow key={c.id} config={c} onDelete={() => setDeleteTarget(c)} />
            ))}
          </tbody>
        </table>
      </div>

      {addOpen && <TelegramConfigModal onClose={() => setAddOpen(false)} />}

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Xóa "${deleteTarget?.name ?? ""}"?`}
        body={<p>Cấu hình sẽ bị xóa khỏi danh sách. Các lịch đăng đã dùng giá trị này trước đó không bị ảnh hưởng.</p>}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
    </section>
  );
}

function TelegramConfigRow({ config, onDelete }: { config: TelegramConfig; onDelete: () => void }) {
  const resolvedChatName = useTelegramChatName(config.bot_token, config.chat_id);

  return (
    <tr>
      <td className="p-3 font-medium">{config.name}</td>
      <td className="p-3 text-slate-600">
        <code className="text-xs bg-slate-100 px-1 rounded">{config.chat_id}</code>
        {resolvedChatName && (
          <div className="text-xs text-brand-600 mt-0.5">{resolvedChatName}</div>
        )}
      </td>
      <td className="p-3 text-slate-500">
        <code className="text-xs">{maskBotToken(config.bot_token)}</code>
      </td>
      <td className="p-3 text-right">
        <button className="text-xs text-red-600" onClick={onDelete}>
          <Trash2 size={14} className="inline" /> Xóa
        </button>
      </td>
    </tr>
  );
}

function formatTelegramApiError(e: any): string {
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc ?? []).join(".")}: ${d.msg}`).join("; ");
  }
  return e?.message || "Request failed";
}

function TelegramConfigModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [detectLoading, setDetectLoading] = useState(false);
  const [detectChats, setDetectChats] = useState<TelegramChatItem[] | null>(null);
  const [detectErr, setDetectErr] = useState<string | null>(null);
  const [testSending, setTestSending] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);

  const createMut = useMutation({
    mutationFn: () => TelegramConfigs.create(name.trim(), botToken.trim(), chatId.trim()),
    onMutate: () => setNotice(null),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["telegram-configs"] });
      onClose();
    },
    onError: (e: any) => setNotice({ ok: false, message: formatTelegramApiError(e) }),
  });

  async function handleDetect() {
    const token = botToken.trim();
    if (!token) return;
    setDetectLoading(true);
    setDetectErr(null);
    try {
      const data = await Telegram.detectChats(token);
      setDetectChats(data.chats);
    } catch (e: any) {
      setDetectChats(null);
      setDetectErr(formatTelegramApiError(e));
    } finally {
      setDetectLoading(false);
    }
  }

  async function handleTest() {
    const token = botToken.trim();
    const chat = chatId.trim();
    if (!token || !chat) return;
    setTestSending(true);
    setNotice(null);
    try {
      const r = await Telegram.sendTest(token, chat);
      setNotice({ ok: r.ok, message: r.message });
    } catch (e: any) {
      setNotice({ ok: false, message: formatTelegramApiError(e) });
    } finally {
      setTestSending(false);
    }
  }

  const canSubmit = name.trim() && botToken.trim() && chatId.trim();
  const resolvedChatName = useTelegramChatName(botToken, chatId, detectChats);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="rounded-lg bg-white shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[calc(100vh-2rem)] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">Thêm cấu hình Telegram</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={20} />
          </button>
        </div>

        <div className="space-y-3">
          <div>
            <label className="label-required">Tên</label>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Kênh chính"
            />
          </div>

          <div>
            <label className="label-required">Bot Token</label>
            <div className="flex gap-2 items-stretch">
              <input
                type="password"
                className="input flex-1 min-w-0"
                value={botToken}
                onChange={(e) => {
                  setBotToken(e.target.value);
                  setDetectChats(null);
                  setDetectErr(null);
                  setNotice(null);
                }}
                placeholder="123456:ABC-DEF..."
              />
              <button
                type="button"
                className="btn-secondary shrink-0 whitespace-nowrap px-3 text-sm"
                disabled={detectLoading || !botToken.trim()}
                onClick={() => void handleDetect()}
              >
                {detectLoading ? "Đang tìm…" : "Dò chat"}
              </button>
            </div>
          </div>

          <div>
            <div className="flex items-center gap-1.5">
              <label className="label-required">Chat ID</label>
              <Tooltip content={<>Chỉ nhập số (kênh private thường bắt đầu bằng <code>-100…</code>).</>}>
                <HelpCircle size={12} className="text-slate-400 cursor-help" />
              </Tooltip>
            </div>
            <div className="flex gap-2 items-stretch">
              <input
                className="input flex-1 min-w-0"
                value={chatId}
                onChange={(e) => {
                  setChatId(e.target.value);
                  setNotice(null);
                }}
                placeholder="-1001234567890"
                inputMode="numeric"
              />
              <button
                type="button"
                className="btn-secondary shrink-0 whitespace-nowrap px-3 text-sm"
                disabled={testSending || !botToken.trim() || !chatId.trim()}
                onClick={() => void handleTest()}
              >
                {testSending ? "Đang gửi…" : "Gửi thử"}
              </button>
            </div>
            {resolvedChatName && (
              <p className="text-xs text-brand-600 mt-1">→ {resolvedChatName}</p>
            )}
          </div>

          {detectChats && detectChats.length > 0 && (
            <div>
              <label className="label">Chọn chat đã tìm thấy</label>
              <select
                className="input"
                key={detectChats.map((c) => c.id).join(",")}
                defaultValue=""
                onChange={(e) => {
                  const v = e.target.value;
                  if (v) setChatId(v);
                }}
              >
                <option value="">— Chọn —</option>
                {detectChats.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {`${c.title} (${c.type}) — ${c.id}`}
                  </option>
                ))}
              </select>
            </div>
          )}
          {detectChats && detectChats.length === 0 && !detectLoading && (
            <p className="text-sm text-amber-700">
              Không tìm thấy chat nào. Hãy gửi một tin nhắn cho bot rồi thử lại.
            </p>
          )}
          {detectErr && <p className="text-sm text-red-600">{detectErr}</p>}

          {notice && (
            <div
              className={
                "text-xs break-words rounded-md px-3 py-2 max-h-32 overflow-y-auto " +
                (notice.ok
                  ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                  : "bg-red-50 text-red-700 border border-red-200")
              }
            >
              <span className="font-medium">{notice.ok ? "✓ " : "✗ "}</span>
              {notice.message}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-3 border-t">
          <button className="btn-secondary" onClick={onClose}>
            Hủy
          </button>
          <button
            className="btn-primary"
            disabled={!canSubmit || createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            Lưu
          </button>
        </div>
      </div>
    </div>
  );
}
