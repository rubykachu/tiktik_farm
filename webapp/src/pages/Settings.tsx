import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Star, Trash2, Edit, X, ExternalLink } from "lucide-react";
import { Settings as SettingsApi } from "../api/settings";
import { LLMModels, LLMModel, LLMModelCreate } from "../api/llmModels";
import ConfirmDialog from "../components/ConfirmDialog";

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
  const { data: models, isLoading } = useQuery({
    queryKey: ["llm-models"],
    queryFn: LLMModels.list,
  });

  const [editState, setEditState] = useState<EditState>(null);
  const [deleteTarget, setDeleteTarget] = useState<LLMModel | null>(null);

  const toggleMut = useMutation({
    mutationFn: (enabled: boolean) => SettingsApi.patch({ llm_enabled: enabled }),
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
      setDeleteTarget(null);
    },
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold">Settings</h2>
        <p className="text-sm text-slate-500 mt-1">
          Configure global behavior and LLM endpoints used for caption rewriting.
        </p>
      </div>

      <section className="card p-6 space-y-4">
        <header className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-medium">LLM caption rewrite</h3>
            <p className="text-sm text-slate-500">
              When enabled, pipelines that use{" "}
              <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> call
              the default model below to rewrite the YouTube title.
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
              {settings?.llm_enabled ? "Enabled" : "Disabled"}
            </span>
          </label>
        </header>

        <div className="flex items-center justify-between border-t pt-4">
          <p className="text-sm text-slate-600">Available models</p>
          <button
            className="btn-primary"
            onClick={() => setEditState({ mode: "create" })}
          >
            <Plus size={16} /> Add model
          </button>
        </div>

        <div className="card p-0 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="text-left p-3">Default</th>
                <th className="text-left p-3">Label</th>
                <th className="text-left p-3">Model ID</th>
                <th className="text-left p-3">Endpoint</th>
                <th className="text-left p-3">API key</th>
                <th className="p-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {isLoading && (
                <tr>
                  <td className="p-4 text-slate-500" colSpan={6}>Loading…</td>
                </tr>
              )}
              {!isLoading && models && models.length === 0 && (
                <tr>
                  <td className="p-6 text-center text-slate-500" colSpan={6}>
                    No models yet. Add one to enable caption rewriting.
                  </td>
                </tr>
              )}
              {models?.map((m) => (
                <tr key={m.id}>
                  <td className="p-3">
                    {m.is_default ? (
                      <span className="chip bg-emerald-100 text-emerald-700">
                        <Star size={12} className="inline mr-1" /> default
                      </span>
                    ) : (
                      <button
                        className="text-xs text-brand-600 hover:underline"
                        onClick={() => setDefaultMut.mutate(m.id)}
                      >
                        Set default
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
                      <Edit size={14} className="inline" /> Edit
                    </button>
                    <button
                      className="text-xs text-red-600"
                      onClick={() => setDeleteTarget(m)}
                    >
                      <Trash2 size={14} className="inline" /> Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <LLMModelModal
        key={editState?.mode === "edit" ? `edit-${editState.model.id}` : "create"}
        state={editState}
        onClose={() => setEditState(null)}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        title={`Delete "${deleteTarget?.label ?? ""}"?`}
        body={
          <p>
            This removes the model from the list. Pipelines currently using{" "}
            <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> will
            fall back to the raw YouTube title until another default is set.
          </p>
        }
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
      />
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
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testOk, setTestOk] = useState<boolean | null>(null);

  const needsEndpoint = modelId.startsWith("azure/");

  const createMut = useMutation({
    mutationFn: (payload: LLMModelCreate) => LLMModels.create(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-models"] });
      onClose();
    },
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
        ...(payload.api_base
          ? { api_base: payload.api_base, api_version: payload.api_version || undefined }
          : { clear_api_base: true }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-models"] });
      onClose();
    },
  });

  const testMut = useMutation({
    mutationFn: () =>
      SettingsApi.testLLM(
        modelId,
        apiKey,
        apiBase || undefined,
        apiVersion || undefined,
      ),
    onMutate: () => {
      setTestOk(null);
      setTestResult(null);
    },
    onSuccess: (r) => {
      setTestOk(r.ok);
      setTestResult(r.message);
    },
    onError: (e: any) => {
      setTestOk(false);
      setTestResult(e?.response?.data?.detail || e?.message || "Test failed");
    },
  });

  if (!state) return null;

  const busy = createMut.isPending || updateMut.isPending;
  const azureReady =
    !needsEndpoint || (apiBase.trim() && apiVersion.trim());
  const canSubmit =
    label.trim() && modelId.trim() && (isEdit || apiKey) && azureReady;

  function resetTest() {
    setTestOk(null);
    setTestResult(null);
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
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 overflow-y-auto py-8">
      <div className="rounded-lg bg-white shadow-xl w-full max-w-md p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">
            {isEdit ? "Edit LLM model" : "Add LLM model"}
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
            <label className="label">Label</label>
            <input
              className="input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Gemini Flash Lite (cheap)"
            />
          </div>
          <div>
            <label className="label">Model ID</label>
            <input
              className="input"
              value={modelId}
              onChange={(e) => {
                setModelId(e.target.value);
                resetTest();
              }}
              placeholder="gemini/gemini-2.0-flash-lite"
            />
            <p className="text-xs text-slate-500 mt-1">
              Format: <code>provider/model</code>. Examples:{" "}
              <code>gemini/gemini-2.0-flash-lite</code>,{" "}
              <code>openai/gpt-4o-mini</code>,{" "}
              <code>azure/my-gpt4o-deployment</code>.{" "}
              <a
                className="text-brand-600 hover:underline inline-flex items-center gap-0.5"
                href="https://docs.litellm.ai/docs/providers"
                target="_blank"
                rel="noopener noreferrer"
              >
                Full list <ExternalLink size={11} />
              </a>
            </p>
          </div>
          <div>
            <label className="label">API key</label>
            <input
              type="password"
              className="input"
              value={apiKey}
              onChange={(e) => {
                setApiKey(e.target.value);
                resetTest();
              }}
              placeholder={
                isEdit ? "Leave empty to keep existing key" : "sk-… or AIza…"
              }
            />
            {isEdit && (
              <p className="text-xs text-slate-500 mt-1">
                Existing key: <code>{existing!.api_key_masked}</code>
              </p>
            )}
          </div>

          {(needsEndpoint || apiBase) && (
            <div className="space-y-3 rounded-md border border-blue-200 bg-blue-50/50 p-3">
              <p className="text-xs font-medium text-blue-700">
                {needsEndpoint
                  ? "Azure endpoint (required for azure/ models)"
                  : "Custom endpoint (optional)"}
              </p>
              <div>
                <label className="label">API base URL</label>
                <input
                  className="input"
                  value={apiBase}
                  onChange={(e) => {
                    setApiBase(e.target.value);
                    resetTest();
                  }}
                  placeholder="https://your-resource.openai.azure.com/"
                />
                {needsEndpoint && (
                  <p className="text-xs text-amber-600 mt-1">
                    Root URL only — do NOT include <code>/openai/v1</code> (litellm
                    adds the path automatically).
                  </p>
                )}
              </div>
              <div>
                <label className="label">API version</label>
                <input
                  className="input"
                  value={apiVersion}
                  onChange={(e) => {
                    setApiVersion(e.target.value);
                    resetTest();
                  }}
                  placeholder="2025-02-01-preview"
                />
                <p className="text-xs text-slate-500 mt-1">
                  Leave empty to use litellm default. Common values:{" "}
                  <code>2025-04-01-preview</code>, <code>2025-02-01-preview</code>,{" "}
                  <code>2024-08-01-preview</code>.
                </p>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2">
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
              {testMut.isPending ? "Testing…" : "Test connection"}
            </button>
            {testResult && (
              <span
                className={
                  "text-xs " + (testOk ? "text-emerald-700" : "text-red-600")
                }
              >
                {testOk ? "✓ " : "✗ "}
                {testResult}
              </span>
            )}
          </div>

          {!isEdit && (
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={setDefault}
                onChange={(e) => setSetDefault(e.target.checked)}
              />
              Set as default
            </label>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-3 border-t">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn-primary"
            disabled={!canSubmit || busy}
            onClick={onSave}
          >
            {isEdit ? "Save" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}
