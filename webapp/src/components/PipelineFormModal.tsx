import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { Accounts } from "../api/client";
import { Pipeline, PipelineCreate, PipelineUpdate, Pipelines } from "../api/pipelines";
import { DouyinCookies } from "../api/douyin_cookies";
import { Telegram, TelegramChatItem } from "../api/telegram";

const baseSchema = z.object({
  name: z.string().min(1, "Required").max(128),
  source_type: z.enum(["youtube", "douyin"]).default("youtube"),
  douyin_cookie_id: z.number().nullable().optional(),
  channel_url: z.string().min(1, "Required"),
  interval_value: z.coerce.number().int().min(1),
  interval_unit: z.enum(["minutes", "hours", "days"]),
  first_fire_at: z.string().optional().default(""),
  fetch_count: z.coerce.number().int().min(1).max(20),
  upload_delay_minutes: z.coerce.number().int().min(0).max(30).default(0),
  max_duration_seconds: z.coerce.number().int().min(1).optional().or(z.literal("")),
  caption_template: z.string().min(1, "Required").max(2200),
  caption_ai_prompt: z.string().max(2000).optional().default(""),
  allow_comment: z.coerce.number().min(0).max(1),
  allow_duet: z.coerce.number().min(0).max(1),
  allow_stitch: z.coerce.number().min(0).max(1),
  visibility_type: z.coerce.number().min(0).max(1),
  account_ids: z.array(z.number()).min(1, "Choose at least one account"),
  telegram_bot_token: z.string().optional().default(""),
  telegram_chat_id: z
    .string()
    .optional()
    .default("")
    .refine((v) => !v || /^-?\d+$/.test(v), {
      message: "Chat ID must be numeric (e.g. -1001234567890)",
    }),
  clear_bot_token: z.boolean().optional().default(false),
});

function buildSchema(existingTokenSet: boolean) {
  return baseSchema
    .refine(
      (d) => d.source_type !== "douyin" || !!d.douyin_cookie_id,
      { message: "Select a Douyin cookie", path: ["douyin_cookie_id"] }
    )
    .refine(
      (d) => {
        if (d.source_type !== "youtube") return true;
        try {
          const u = new URL(d.channel_url);
          return ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"].includes(u.hostname);
        } catch { return false; }
      },
      { message: "Must be a YouTube URL (e.g. https://youtube.com/@channel)", path: ["channel_url"] }
    )
    .refine(
      (d) => {
        if (d.source_type !== "douyin") return true;
        try {
          const u = new URL(d.channel_url);
          return ["douyin.com", "www.douyin.com", "m.douyin.com", "v.douyin.com"].includes(u.hostname);
        } catch { return false; }
      },
      { message: "Must be a Douyin URL (e.g. https://www.douyin.com/user/...)", path: ["channel_url"] }
    )
    .refine((d) => !(d.interval_unit === "minutes" && d.interval_value < 2), {
      message: "Interval must be ≥ 2 when using minutes",
      path: ["interval_value"],
    })
    .refine(
      (d) => d.interval_unit !== "days" || /^\d{2}:\d{2}$/.test(d.first_fire_at ?? ""),
      { message: "Enter a daily run time (HH:MM)", path: ["first_fire_at"] }
    )
    .refine(
      (d) => {
        // {ai_title} in the template requires a non-empty AI prompt.
        if (!d.caption_template.includes("{ai_title}")) return true;
        return !!(d.caption_ai_prompt && d.caption_ai_prompt.trim());
      },
      {
        message:
          "Add an AI prompt or remove {ai_title} from the caption template",
        path: ["caption_ai_prompt"],
      }
    )
    .refine(
      (d) => {
        if (d.clear_bot_token) {
          return !d.telegram_chat_id;
        }
        const botPresent =
          !!d.telegram_bot_token || (existingTokenSet && !d.clear_bot_token);
        const chatSet = !!d.telegram_chat_id;
        return botPresent === chatSet;
      },
      {
        message: "Bot token and chat ID must be set together (or both empty)",
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

function formatHttpDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((e) => {
        if (e && typeof e === "object" && "msg" in e)
          return String((e as { msg: string }).msg);
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

function toLocalTime(iso?: string): string {
  const d = iso ? new Date(iso) : new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function getTimezoneLabel(): string {
  const offset = -new Date().getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const h = Math.floor(Math.abs(offset) / 60).toString().padStart(2, "0");
  const m = (Math.abs(offset) % 60).toString().padStart(2, "0");
  return `GMT${sign}${h}:${m}`;
}

function timeToNextOccurrence(timeStr: string): Date {
  const [h, m] = timeStr.split(":").map(Number);
  const next = new Date();
  next.setHours(h, m, 0, 0);
  if (next <= new Date()) next.setDate(next.getDate() + 1);
  return next;
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

  const { data: douyinCookies } = useQuery({
    queryKey: ["douyin-cookies"],
    queryFn: DouyinCookies.list,
    enabled: open,
  });

  const tokenSet = pipeline?.telegram_bot_token_set ?? false;
  const schema = useMemo(() => buildSchema(tokenSet), [tokenSet]);

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    setError,
    getValues,
    trigger,
    formState: { errors, touchedFields },
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    mode: "onBlur",
    defaultValues: {
      name: "",
      source_type: "youtube" as "youtube" | "douyin",
      douyin_cookie_id: null,
      channel_url: "",
      interval_value: 6,
      interval_unit: "hours",
      first_fire_at: "",
      fetch_count: 1,
      upload_delay_minutes: 0,
      max_duration_seconds: "" as any,
      caption_template: "{title}",
      caption_ai_prompt: "",
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

      let uploadOpts = {
        allow_comment: 1,
        allow_duet: 0,
        allow_stitch: 0,
        visibility_type: 0,
      };
      try {
        uploadOpts = { ...uploadOpts, ...JSON.parse(pipeline.upload_options_json) };
      } catch {}

      reset({
        name: pipeline.name,
        source_type: (pipeline.source_type ?? "youtube") as "youtube" | "douyin",
        douyin_cookie_id: pipeline.douyin_cookie_id ?? null,
        channel_url: pipeline.channel_url,
        interval_value: pipeline.interval_value,
        interval_unit: pipeline.interval_unit,
        first_fire_at: pipeline.interval_unit === "days" ? toLocalTime(pipeline.first_fire_at) : "",
        fetch_count: pipeline.fetch_count,
        upload_delay_minutes: pipeline.upload_delay_minutes ?? 0,
        max_duration_seconds: pipeline.max_duration_seconds ?? ("" as any),
        caption_template: pipeline.caption_template,
        caption_ai_prompt: pipeline.caption_ai_prompt ?? "",
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
        source_type: "youtube",
        douyin_cookie_id: null,
        channel_url: "",
        interval_value: 6,
        interval_unit: "hours",
        first_fire_at: "",
        fetch_count: 1,
        upload_delay_minutes: 0,
        max_duration_seconds: "" as any,
        caption_template: "{title}",
        caption_ai_prompt: "",
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
        const loc: string[] = e.loc ?? [];
        // Strip leading "body" segment added by FastAPI
        const fieldLoc = loc.filter((s) => s !== "body");
        const field = fieldLoc[fieldLoc.length - 1] || "channel_url";
        setError(field as any, { message: e.msg });
      }
    } else if (typeof detail === "string") {
      setError("name", { message: detail });
    }
  }

  function onSubmit(data: FormData, saveState: "active" | "paused") {
    const firstFireAt =
      data.interval_unit === "days" && data.first_fire_at
        ? timeToNextOccurrence(data.first_fire_at).toISOString()
        : new Date().toISOString();
    const maxDur =
      data.max_duration_seconds === "" || data.max_duration_seconds === undefined
        ? undefined
        : Number(data.max_duration_seconds);
    const aiPrompt = (data.caption_ai_prompt || "").trim() || null;

    if (isEdit && pipeline) {
      const payload: PipelineUpdate = {
        name: data.name,
        state: saveState,
        channel_url: data.channel_url,
        douyin_cookie_id: data.source_type === "douyin" ? (data.douyin_cookie_id ?? null) : undefined,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        first_fire_at: firstFireAt,
        caption_template: data.caption_template,
        caption_ai_prompt: aiPrompt,
        upload_options: {
          allow_comment: data.allow_comment as 0 | 1,
          allow_duet: data.allow_duet as 0 | 1,
          allow_stitch: data.allow_stitch as 0 | 1,
          visibility_type: data.visibility_type as 0 | 1,
        },
        account_ids: data.account_ids,
        max_duration_seconds: maxDur ?? null,
        upload_delay_minutes: data.upload_delay_minutes,
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
        source_type: data.source_type,
        douyin_cookie_id: data.source_type === "douyin" ? (data.douyin_cookie_id ?? null) : undefined,
        channel_url: data.channel_url,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        first_fire_at: firstFireAt,
        caption_template: data.caption_template,
        caption_ai_prompt: aiPrompt ?? undefined,
        upload_options: {
          allow_comment: data.allow_comment as 0 | 1,
          allow_duet: data.allow_duet as 0 | 1,
          allow_stitch: data.allow_stitch as 0 | 1,
          visibility_type: data.visibility_type as 0 | 1,
        },
        account_ids: data.account_ids,
        max_duration_seconds: maxDur,
        upload_delay_minutes: data.upload_delay_minutes,
        telegram_bot_token: data.telegram_bot_token || undefined,
        telegram_chat_id: data.telegram_chat_id || undefined,
      };
      createMut.mutate(payload);
    }
  }

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [aiSectionOpen, setAiSectionOpen] = useState(false);
  const [telegramDetectLoading, setTelegramDetectLoading] = useState(false);
  const [telegramDetectChats, setTelegramDetectChats] = useState<
    TelegramChatItem[] | null
  >(null);
  const [telegramDetectErr, setTelegramDetectErr] = useState<string | null>(null);
  const [telegramTestSending, setTelegramTestSending] = useState(false);

  const selectedAccounts = watch("account_ids");
  const sourceType = watch("source_type");
  const fetchCount = watch("fetch_count");
  const clearBotToken = watch("clear_bot_token");
  const intervalValue = watch("interval_value");
  const intervalUnit = watch("interval_unit");
  const telegramBotToken = watch("telegram_bot_token");
  const telegramChatId = watch("telegram_chat_id");
  const captionTemplate = watch("caption_template");
  const captionAiPrompt = watch("caption_ai_prompt");

  // Auto-open AI section if the user already has a prompt or the template uses {ai_title}.
  useEffect(() => {
    if (
      open &&
      ((captionAiPrompt && captionAiPrompt.trim()) ||
        (captionTemplate && captionTemplate.includes("{ai_title}")))
    ) {
      setAiSectionOpen(true);
    }
  }, [open, pipeline?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) {
      setTelegramDetectChats(null);
      setTelegramDetectErr(null);
      setTelegramDetectLoading(false);
      setTelegramTestSending(false);
      setAdvancedOpen(false);
      setAiSectionOpen(false);
    }
  }, [open]);

  // Resync douyin_cookie_id after cookies load (reset() may fire before query resolves)
  useEffect(() => {
    if (open && pipeline?.douyin_cookie_id && douyinCookies?.length) {
      setValue("douyin_cookie_id", pipeline.douyin_cookie_id);
    }
  }, [douyinCookies, open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-validate URL when source type changes (domain rules differ per type)
  useEffect(() => {
    if (open && touchedFields.channel_url) trigger("channel_url");
  }, [sourceType]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open) return;
    setTelegramDetectChats(null);
    setTelegramDetectErr(null);
  }, [telegramBotToken, open]);

  const repeatHint = useMemo(() => {
    const n = Number(intervalValue);
    if (!Number.isFinite(n) || n < 1) return null;
    const unitLabel =
      intervalUnit === "minutes" ? "minutes" : intervalUnit === "days" ? "days" : "hours";
    return `Pipeline runs every ${n} ${unitLabel} from this anchor while active.`;
  }, [intervalValue, intervalUnit]);

  async function handleTelegramDetect() {
    const token = getValues("telegram_bot_token").trim();
    if (!token || clearBotToken) return;
    setTelegramDetectLoading(true);
    setTelegramDetectErr(null);
    try {
      const data = await Telegram.detectChats(token);
      setTelegramDetectChats(data.chats);
    } catch (e: unknown) {
      setTelegramDetectChats(null);
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail;
      setTelegramDetectErr(formatHttpDetail(d, "Detection failed. Try again later."));
    } finally {
      setTelegramDetectLoading(false);
    }
  }

  async function handleTelegramTest() {
    const token = getValues("telegram_bot_token").trim();
    const chatId = getValues("telegram_chat_id").trim();
    if (!token || !chatId || clearBotToken) return;
    setTelegramTestSending(true);
    try {
      const r = await Telegram.sendTest(token, chatId);
      toast.success("Telegram test sent", { description: r.message });
    } catch (e: unknown) {
      const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data
        ?.detail;
      toast.error("Sending test message failed", {
        description: formatHttpDetail(d, "Sending test message failed."),
      });
    } finally {
      setTelegramTestSending(false);
    }
  }

  if (!open) return null;

  const isBusy = createMut.isPending || updateMut.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 overflow-y-auto py-8">
      <div
        className="rounded-lg bg-white shadow-xl w-full max-w-4xl p-6 space-y-5"
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

        <form
          className="grid lg:grid-cols-2 gap-x-6 gap-y-5"
          onSubmit={(e) => e.preventDefault()}
        >
          {/* Basic */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Basic</legend>
            <div>
              <label className="label">Name</label>
              <input className="input" {...register("name")} placeholder="My pipeline" />
              {errors.name && <p className="field-error">{errors.name.message}</p>}
            </div>
            {!isEdit && (
              <div>
                <label className="label">Source type</label>
                <select className="input" {...register("source_type")}>
                  <option value="youtube">YouTube</option>
                  <option value="douyin">Douyin</option>
                </select>
              </div>
            )}
            {sourceType === "douyin" && (
              <div>
                <label className="label">Douyin cookie</label>
                <select
                  className="input"
                  {...register("douyin_cookie_id", { valueAsNumber: true })}
                >
                  <option value="">— Select cookie —</option>
                  {douyinCookies?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
                {errors.douyin_cookie_id && (
                  <p className="field-error">{errors.douyin_cookie_id.message}</p>
                )}
                {(!douyinCookies || douyinCookies.length === 0) && (
                  <p className="text-xs text-amber-600 mt-1">
                    No Douyin cookies yet. Add one in{" "}
                    <Link to="/accounts" className="text-brand-600 hover:underline">
                      Accounts → Douyin Cookies
                    </Link>
                    .
                  </p>
                )}
              </div>
            )}
            <div>
              <label className="label">
                {sourceType === "douyin" ? "Douyin creator URL" : "Channel URL"}
              </label>
              <input
                className="input"
                {...register("channel_url")}
                placeholder={
                  sourceType === "douyin"
                    ? "https://www.douyin.com/user/MS4wLjAB..."
                    : "https://youtube.com/@channel/videos"
                }
              />
              {errors.channel_url && (
                <p className="field-error">{errors.channel_url.message}</p>
              )}
            </div>
          </fieldset>

          {/* Trigger */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Trigger</legend>
            <div className="flex gap-3 items-end">
              <div className="flex-1">
                <label className="label">Every</label>
                <input
                  type="number"
                  className="input"
                  min={1}
                  {...register("interval_value")}
                />
              </div>
              <div className="flex-1">
                <label className="label">Unit</label>
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
            {intervalUnit === "days" && (
              <div>
                <label className="label">Run daily at ({getTimezoneLabel()})</label>
                <input
                  type="time"
                  className="input"
                  {...register("first_fire_at")}
                />
                {errors.first_fire_at && (
                  <p className="field-error">{errors.first_fire_at.message}</p>
                )}
                <p className="text-xs text-slate-400 mt-1">
                  If this time has already passed today, the first run will be tomorrow.
                </p>
              </div>
            )}
            {repeatHint && (
              <p className="text-xs text-slate-500 mt-1">{repeatHint}</p>
            )}
          </fieldset>

          {/* Content */}
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-slate-700">Content</legend>
            <div>
              <label className="label">Videos per run (1-20)</label>
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
            {Number(fetchCount) > 1 && (
              <div>
                <label className="label">Rest between uploads (minutes)</label>
                <input
                  type="number"
                  className="input"
                  min={0}
                  max={30}
                  {...register("upload_delay_minutes")}
                />
                <p className="text-xs text-slate-400 mt-1">
                  Minutes to wait between uploads (0 = no wait). Helps avoid spam
                  detection by TikTok.
                </p>
                {errors.upload_delay_minutes && (
                  <p className="field-error">{errors.upload_delay_minutes.message}</p>
                )}
              </div>
            )}
            <div>
              <label className="label">Max duration (seconds, optional)</label>
              <input
                type="number"
                className="input"
                min={1}
                {...register("max_duration_seconds")}
                placeholder="Leave empty for no limit"
              />
              <p className="text-xs text-slate-400 mt-1">
                When set, yt-dlp skips videos longer than 3× this value and trims
                the rest to the first N seconds.
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
                Variables:{" "}
                <code className="bg-slate-100 px-1 rounded">{"{title}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{title_hashtag}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{now}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code>
              </p>
            </div>

            <button
              type="button"
              className="flex items-center gap-1 text-sm font-medium text-slate-700"
              onClick={() => setAdvancedOpen(!advancedOpen)}
            >
              {advancedOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              Advanced flags
            </button>
            {advancedOpen && (
              <div className="grid grid-cols-2 gap-3">
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

          {/* AI rewrite (optional, full-width) */}
          <fieldset className="lg:col-span-2 space-y-3">
            <button
              type="button"
              className="flex items-center gap-1 text-sm font-medium text-slate-700"
              onClick={() => setAiSectionOpen(!aiSectionOpen)}
            >
              {aiSectionOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              AI caption rewrite (optional)
            </button>
            {aiSectionOpen && (
              <div className="space-y-2">
                <label className="label">Prompt</label>
                <textarea
                  className="input min-h-[80px]"
                  maxLength={2000}
                  {...register("caption_ai_prompt")}
                  placeholder="Rewrite the title in a clickbait style for TikTok."
                />
                {errors.caption_ai_prompt && (
                  <p className="field-error">{errors.caption_ai_prompt.message}</p>
                )}
                <p className="text-xs text-slate-500">
                  The output is available as{" "}
                  <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> in
                  the caption template. Configure the default model in{" "}
                  <Link to="/settings" className="text-brand-600 hover:underline">
                    Settings
                  </Link>
                  .
                </p>
              </div>
            )}
          </fieldset>

          {/* Accounts (full-width) */}
          <fieldset className="lg:col-span-2 space-y-2">
            <legend className="text-sm font-medium text-slate-700">
              Accounts{" "}
              <span className="text-slate-400 font-normal">
                ({selectedAccounts.length} selected)
              </span>
            </legend>
            {validAccounts.length === 0 && (
              <p className="text-xs text-amber-600">
                No accounts with a valid session. Import a cookie first.
              </p>
            )}
            <div className="max-h-40 overflow-y-auto border rounded p-2 grid grid-cols-2 gap-1">
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

          {/* Telegram (optional, full-width) */}
          <fieldset className="lg:col-span-2 space-y-3">
            <legend className="text-sm font-medium text-slate-700">
              Telegram <span className="text-slate-400 font-normal">(optional)</span>
            </legend>
            <div className="grid lg:grid-cols-2 gap-3">
              <div>
                <label className="label">Bot token</label>
                <div className="flex gap-2 items-stretch">
                  <input
                    type="password"
                    className="input flex-1 min-w-0"
                    {...register("telegram_bot_token")}
                    placeholder={
                      isEdit && pipeline?.telegram_bot_token_set
                        ? "••• (configured)"
                        : "123456:ABC-DEF..."
                    }
                    disabled={clearBotToken}
                  />
                  <button
                    type="button"
                    className="btn-secondary shrink-0 whitespace-nowrap px-3 text-sm"
                    disabled={
                      clearBotToken ||
                      telegramDetectLoading ||
                      !String(telegramBotToken || "").trim()
                    }
                    onClick={() => {
                      void handleTelegramDetect();
                    }}
                  >
                    {telegramDetectLoading ? "Loading…" : "Auto-detect"}
                  </button>
                </div>
                {isEdit &&
                  pipeline?.telegram_bot_token_set &&
                  !String(telegramBotToken || "").trim() && (
                    <p className="text-xs text-slate-500 mt-1">
                      Re-enter the bot token to use this feature.
                    </p>
                  )}
              </div>
              <div>
                <label className="label">Chat ID</label>
                <div className="flex gap-2 items-stretch">
                  <input
                    className="input flex-1 min-w-0"
                    {...register("telegram_chat_id")}
                    placeholder="-1001234567890"
                    inputMode="numeric"
                  />
                  <button
                    type="button"
                    className="btn-secondary shrink-0 whitespace-nowrap px-3 text-sm"
                    disabled={
                      telegramTestSending ||
                      clearBotToken ||
                      !String(telegramBotToken || "").trim() ||
                      !String(telegramChatId || "").trim()
                    }
                    onClick={() => {
                      void handleTelegramTest();
                    }}
                  >
                    {telegramTestSending ? "Sending…" : "Send test"}
                  </button>
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  Numeric only (private channels usually start with{" "}
                  <code className="bg-slate-100 px-1 rounded">-100…</code>). Add the
                  bot as a channel admin.
                </p>
                {errors.telegram_chat_id && (
                  <p className="field-error">{errors.telegram_chat_id.message}</p>
                )}
              </div>
            </div>
            {telegramDetectChats && telegramDetectChats.length > 0 && (
              <div>
                <label className="label">Pick a detected chat</label>
                <select
                  className="input"
                  key={telegramDetectChats.map((c) => c.id).join(",")}
                  defaultValue=""
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) {
                      setValue("telegram_chat_id", v, { shouldValidate: true });
                    }
                  }}
                >
                  <option value="">— Select —</option>
                  {telegramDetectChats.map((c) => (
                    <option key={c.id} value={String(c.id)}>
                      {`${c.title} (${c.type}) — ${c.id}`}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {telegramDetectChats &&
              telegramDetectChats.length === 0 &&
              !telegramDetectLoading && (
                <p className="text-sm text-amber-700">
                  No channels found. Add the bot as a channel admin and send at
                  least one message.
                </p>
              )}
            {telegramDetectErr && (
              <p className="text-sm text-red-600">{telegramDetectErr}</p>
            )}
            {isEdit && pipeline?.telegram_bot_token_set && (
              <label className="flex items-center gap-2 text-sm text-red-600">
                <input type="checkbox" {...register("clear_bot_token")} />
                Remove bot token
              </label>
            )}
            {errors.telegram_bot_token && (
              <p className="field-error">{errors.telegram_bot_token.message}</p>
            )}
          </fieldset>

          {/* Actions */}
          <div className="lg:col-span-2 flex justify-end gap-2 pt-3 border-t">
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
