import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import { Accounts } from "../api/client";
import { Pipeline, PipelineCreate, PipelineUpdate, Pipelines } from "../api/pipelines";

const baseSchema = z.object({
  name: z.string().min(1, "Bắt buộc").max(128),
  channel_url: z.string().min(1, "Bắt buộc").url("URL không hợp lệ"),
  interval_value: z.coerce.number().int().min(1),
  interval_unit: z.enum(["minutes", "hours", "days"]),
  first_fire_at: z.string().min(1, "Bắt buộc"),
  fetch_count: z.coerce.number().int().min(1).max(20),
  max_duration_seconds: z.coerce.number().int().min(1).optional().or(z.literal("")),
  caption_template: z.string().min(1, "Bắt buộc").max(2200),
  allow_comment: z.coerce.number().min(0).max(1),
  allow_duet: z.coerce.number().min(0).max(1),
  allow_stitch: z.coerce.number().min(0).max(1),
  visibility_type: z.coerce.number().min(0).max(1),
  account_ids: z.array(z.number()).min(1, "Chọn ít nhất 1 account"),
  telegram_bot_token: z.string().optional().default(""),
  telegram_chat_id: z.string().optional().default(""),
  clear_bot_token: z.boolean().optional().default(false),
});

function buildSchema(existingTokenSet: boolean) {
  return baseSchema
    .refine(
      (d) => !(d.interval_unit === "minutes" && d.interval_value < 2),
      { message: "Interval ≥ 2 khi chọn minutes", path: ["interval_value"] }
    )
    .refine(
      (d) => {
        if (d.clear_bot_token) {
          // Clearing token → chat_id must also be empty
          return !d.telegram_chat_id;
        }
        // Token is "present" if user typed one OR existing token is kept
        const botPresent = !!d.telegram_bot_token || (existingTokenSet && !d.clear_bot_token);
        const chatSet = !!d.telegram_chat_id;
        return botPresent === chatSet;
      },
      {
        message: "Phải nhập cả 2 hoặc bỏ trống cả 2",
        path: ["telegram_bot_token"],
      }
    );
}

type FormData = z.infer<typeof baseSchema>;

type Props = {
  open: boolean;
  pipeline?: Pipeline | null;
  onClose: () => void;
};

function toLocalDatetime(iso?: string): string {
  if (!iso) {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    return now.toISOString().slice(0, 16);
  }
  const d = new Date(iso);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export default function PipelineFormModal({ open, pipeline, onClose }: Props) {
  const qc = useQueryClient();
  const isEdit = !!pipeline;

  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: Accounts.list,
    enabled: open,
  });

  const validAccounts = accounts?.filter((a) => a.has_valid_session) ?? [];

  const tokenSet = pipeline?.telegram_bot_token_set ?? false;
  const schema = useMemo(() => buildSchema(tokenSet), [tokenSet]);

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    setError,
    formState: { errors },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      channel_url: "",
      interval_value: 6,
      interval_unit: "hours",
      first_fire_at: toLocalDatetime(),
      fetch_count: 3,
      max_duration_seconds: "" as any,
      caption_template: "{title}",
      allow_comment: 1,
      allow_duet: 0,
      allow_stitch: 0,
      visibility_type: 0,
      account_ids: [],
      telegram_bot_token: "",
      telegram_chat_id: "",
      clear_bot_token: false,
    },
  });

  useEffect(() => {
    if (!open) return;
    if (pipeline) {
      let accountIds: number[] = [];
      try {
        accountIds = JSON.parse(pipeline.account_ids_json);
      } catch {}

      let uploadOpts = { allow_comment: 1, allow_duet: 0, allow_stitch: 0, visibility_type: 0 };
      try {
        uploadOpts = { ...uploadOpts, ...JSON.parse(pipeline.upload_options_json) };
      } catch {}

      reset({
        name: pipeline.name,
        channel_url: pipeline.channel_url,
        interval_value: pipeline.interval_value,
        interval_unit: pipeline.interval_unit,
        first_fire_at: toLocalDatetime(pipeline.first_fire_at),
        fetch_count: pipeline.fetch_count,
        max_duration_seconds: pipeline.max_duration_seconds ?? ("" as any),
        caption_template: pipeline.caption_template,
        allow_comment: uploadOpts.allow_comment as 0 | 1,
        allow_duet: uploadOpts.allow_duet as 0 | 1,
        allow_stitch: uploadOpts.allow_stitch as 0 | 1,
        visibility_type: uploadOpts.visibility_type as 0 | 1,
        account_ids: accountIds,
        telegram_bot_token: "",
        telegram_chat_id: pipeline.telegram_chat_id ?? "",
        clear_bot_token: false,
      });
    } else {
      reset({
        name: "",
        channel_url: "",
        interval_value: 6,
        interval_unit: "hours",
        first_fire_at: toLocalDatetime(),
        fetch_count: 3,
        max_duration_seconds: "" as any,
        caption_template: "{title}",
        allow_comment: 1,
        allow_duet: 0,
        allow_stitch: 0,
        visibility_type: 0,
        account_ids: [],
        telegram_bot_token: "",
        telegram_chat_id: "",
        clear_bot_token: false,
      });
    }
  }, [open, pipeline, reset]);

  const createMut = useMutation({
    mutationFn: (p: PipelineCreate) => Pipelines.create(p),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      onClose();
    },
    onError: handleServerError,
  });

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: PipelineUpdate }) =>
      Pipelines.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      onClose();
    },
    onError: handleServerError,
  });

  function handleServerError(err: any) {
    const detail = err?.response?.data?.detail;
    if (Array.isArray(detail)) {
      for (const e of detail) {
        const field = e.loc?.[e.loc.length - 1];
        if (field) {
          setError(field as any, { message: e.msg });
        }
      }
    } else if (typeof detail === "string") {
      setError("name", { message: detail });
    }
  }

  function onSubmit(data: FormData, saveState: "active" | "paused") {
    const firstFireAt = new Date(data.first_fire_at).toISOString();
    const maxDur =
      data.max_duration_seconds === "" || data.max_duration_seconds === undefined
        ? undefined
        : Number(data.max_duration_seconds);

    if (isEdit && pipeline) {
      const payload: PipelineUpdate = {
        name: data.name,
        state: saveState,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        caption_template: data.caption_template,
        upload_options: {
          allow_comment: data.allow_comment as 0 | 1,
          allow_duet: data.allow_duet as 0 | 1,
          allow_stitch: data.allow_stitch as 0 | 1,
          visibility_type: data.visibility_type as 0 | 1,
        },
        account_ids: data.account_ids,
        max_duration_seconds: maxDur ?? null,
        telegram_chat_id: data.telegram_chat_id || undefined,
        clear_bot_token: data.clear_bot_token,
      };
      if (data.telegram_bot_token) {
        payload.telegram_bot_token = data.telegram_bot_token;
      }
      updateMut.mutate({ id: pipeline.id, data: payload });
    } else {
      const payload: PipelineCreate = {
        name: data.name,
        channel_url: data.channel_url,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        first_fire_at: firstFireAt,
        caption_template: data.caption_template,
        upload_options: {
          allow_comment: data.allow_comment as 0 | 1,
          allow_duet: data.allow_duet as 0 | 1,
          allow_stitch: data.allow_stitch as 0 | 1,
          visibility_type: data.visibility_type as 0 | 1,
        },
        account_ids: data.account_ids,
        max_duration_seconds: maxDur,
        telegram_bot_token: data.telegram_bot_token || undefined,
        telegram_chat_id: data.telegram_chat_id || undefined,
      };
      createMut.mutate(payload);
    }
  }

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const selectedAccounts = watch("account_ids");
  const clearBotToken = watch("clear_bot_token");

  if (!open) return null;

  const isBusy = createMut.isPending || updateMut.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 overflow-y-auto py-8">
      <div
        className="rounded-lg bg-white shadow-xl w-full max-w-lg p-6 space-y-5"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold">
            {isEdit ? "Edit pipeline" : "New pipeline"}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={20} />
          </button>
        </div>

        <form className="space-y-5" onSubmit={(e) => e.preventDefault()}>
          {/* Basic */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Basic</legend>
            <div>
              <label className="label">Name</label>
              <input className="input" {...register("name")} placeholder="My pipeline" />
              {errors.name && <p className="field-error">{errors.name.message}</p>}
            </div>
            <div>
              <label className="label">Channel URL</label>
              <input
                className="input"
                {...register("channel_url")}
                placeholder="https://youtube.com/@channel/videos"
                disabled={isEdit}
              />
              {errors.channel_url && (
                <p className="field-error">{errors.channel_url.message}</p>
              )}
              {isEdit && (
                <p className="text-xs text-slate-400 mt-1">
                  Channel URL không đổi được sau khi tạo.
                </p>
              )}
            </div>
          </fieldset>

          {/* Trigger */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Trigger</legend>
            <div className="flex gap-3 items-end">
              <div className="flex-1">
                <label className="label">Lặp mỗi</label>
                <input
                  type="number"
                  className="input"
                  min={1}
                  {...register("interval_value")}
                />
              </div>
              <div className="flex-1">
                <label className="label">Đơn vị</label>
                <select className="input" {...register("interval_unit")}>
                  <option value="minutes">minutes</option>
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                </select>
              </div>
            </div>
            {errors.interval_value && (
              <p className="field-error">{errors.interval_value.message}</p>
            )}
            <div>
              <label className="label">Bắt đầu lúc</label>
              <input
                type="datetime-local"
                className="input"
                {...register("first_fire_at")}
                disabled={isEdit}
              />
              {isEdit && (
                <p className="text-xs text-slate-400 mt-1">
                  Thời gian bắt đầu không thể thay đổi.
                </p>
              )}
            </div>
          </fieldset>

          {/* Content */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Content</legend>
            <div>
              <label className="label">Số video mỗi lần (1-20)</label>
              <input
                type="number"
                className="input"
                min={1}
                max={20}
                {...register("fetch_count")}
              />
              {errors.fetch_count && (
                <p className="field-error">{errors.fetch_count.message}</p>
              )}
            </div>
            <div>
              <label className="label">Max duration (giây) — optional</label>
              <input
                type="number"
                className="input"
                min={1}
                {...register("max_duration_seconds")}
                placeholder="Bỏ trống = không giới hạn"
              />
              <p className="text-xs text-slate-400 mt-1">
                Nếu set, yt-dlp tự skip video dài quá 3×N và trim 0..N giây.
              </p>
            </div>
          </fieldset>

          {/* Caption */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Caption</legend>
            <div>
              <textarea
                className="input min-h-[80px]"
                maxLength={2200}
                {...register("caption_template")}
                placeholder="{title} #hashtag"
              />
              {errors.caption_template && (
                <p className="field-error">{errors.caption_template.message}</p>
              )}
              <p className="text-xs text-slate-400 mt-1">
                Biến hỗ trợ:{" "}
                <code className="bg-slate-100 px-1 rounded">{"title"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"title_hashtag"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"now"}</code>
              </p>
            </div>
          </fieldset>

          {/* Advanced (collapsible) */}
          <fieldset>
            <button
              type="button"
              className="flex items-center gap-1 text-sm font-medium text-slate-700"
              onClick={() => setAdvancedOpen(!advancedOpen)}
            >
              {advancedOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              Advanced
            </button>
            {advancedOpen && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" value={1} {...register("allow_comment")} defaultChecked />
                  Allow comment
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" value={1} {...register("allow_duet")} />
                  Allow duet
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" value={1} {...register("allow_stitch")} />
                  Allow stitch
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" value={1} {...register("visibility_type")} />
                  Private
                </label>
              </div>
            )}
          </fieldset>

          {/* Accounts */}
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-slate-700">
              Accounts{" "}
              <span className="text-slate-400 font-normal">
                ({selectedAccounts.length} selected)
              </span>
            </legend>
            {validAccounts.length === 0 && (
              <p className="text-xs text-amber-600">
                Không có account nào có session hợp lệ. Import cookie trước.
              </p>
            )}
            <div className="max-h-40 overflow-y-auto border rounded p-2 space-y-1">
              {validAccounts.map((a) => (
                <label key={a.id} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    value={a.id}
                    checked={selectedAccounts.includes(a.id)}
                    onChange={(e) => {
                      const id = a.id;
                      if (e.target.checked) {
                        setValue("account_ids", [...selectedAccounts, id], {
                          shouldValidate: true,
                        });
                      } else {
                        setValue(
                          "account_ids",
                          selectedAccounts.filter((x) => x !== id),
                          { shouldValidate: true }
                        );
                      }
                    }}
                  />
                  {a.display_name || a.username}
                </label>
              ))}
            </div>
            {errors.account_ids && (
              <p className="field-error">{errors.account_ids.message}</p>
            )}
          </fieldset>

          {/* Telegram (optional) */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">
              Telegram <span className="text-slate-400 font-normal">(optional)</span>
            </legend>
            <div>
              <label className="label">Bot token</label>
              <input
                type="password"
                className="input"
                {...register("telegram_bot_token")}
                placeholder={
                  isEdit && pipeline?.telegram_bot_token_set
                    ? "••• (đã cấu hình)"
                    : "123456:ABC-DEF..."
                }
                disabled={clearBotToken}
              />
            </div>
            <div>
              <label className="label">Chat ID</label>
              <input
                className="input"
                {...register("telegram_chat_id")}
                placeholder="-1001234567890 hoặc @channel"
              />
            </div>
            {isEdit && pipeline?.telegram_bot_token_set && (
              <label className="flex items-center gap-2 text-sm text-red-600">
                <input type="checkbox" {...register("clear_bot_token")} />
                Xóa bot token
              </label>
            )}
            {errors.telegram_bot_token && (
              <p className="field-error">{errors.telegram_bot_token.message}</p>
            )}
          </fieldset>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-3 border-t">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={isBusy}
              onClick={handleSubmit((d) => onSubmit(d, "paused"))}
            >
              Save as paused
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white"
              disabled={isBusy}
              onClick={handleSubmit((d) => onSubmit(d, "active"))}
            >
              {isEdit ? "Save & activate" : "Create & activate"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
