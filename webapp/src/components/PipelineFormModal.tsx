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
import { Settings } from "../api/settings";

const baseSchema = z.object({
  name: z.string().min(1, "Required").max(128),
  source_type: z.enum(["youtube", "douyin"]).default("youtube"),
  douyin_cookie_id: z.number().nullable().optional(),
  channel_url: z.string().min(1, "Required"),
  interval_value: z.coerce.number().int().min(1),
  interval_unit: z.enum(["minutes", "days"]),
  first_fire_at: z.string().optional().default(""),
  fetch_count: z.coerce.number().int().min(1).max(20),
  upload_delay_minutes: z.coerce.number().int().min(0).max(30).default(0),
  max_duration_seconds: z.coerce.number().int().min(1).optional().or(z.literal("")),
  caption_template: z.string().min(1, "Required").max(2200),
  caption_ai_prompt: z.string().max(2000).optional().default(""),
  channel_description: z.string().max(2000).optional().default(""),
  allow_comment: z.coerce.number().min(0).max(1),
  allow_duet: z.coerce.number().min(0).max(1),
  allow_stitch: z.coerce.number().min(0).max(1),
  visibility_type: z.coerce.number().min(0).max(1),
  account_ids: z.array(z.number()).min(1, "Choose at least one account"),
  window_end_time: z.string().optional().default(""),
  window_delay_minutes: z.coerce.number().int().min(3).optional().or(z.literal("")),
  telegram_bot_token: z.string().optional().default(""),
  telegram_chat_id: z
    .string()
    .optional()
    .default("")
    .refine((v) => !v || /^-?\d+$/.test(v), {
      message: "Chat ID must be numeric (e.g. -1001234567890)",
    }),
  clear_bot_token: z.boolean().optional().default(false),
  sync_from_date: z.string().optional().default(""),
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
      (d) => {
        const endSet = !!(d.window_end_time && d.window_end_time.trim());
        const delaySet = !!(d.window_delay_minutes !== "" && d.window_delay_minutes !== undefined);
        return endSet === delaySet;
      },
      { message: "Window end time and repeat interval must both be set or both be empty", path: ["window_end_time"] }
    )
    .refine(
      (d) => {
        if (!d.window_end_time || !d.first_fire_at) return true;
        return toMinutes(d.window_end_time) > toMinutes(d.first_fire_at);
      },
      { message: "Window end must be later than the start time", path: ["window_end_time"] }
    )
    .refine(
      (d) => {
        if (!d.window_end_time || !d.first_fire_at || !d.window_delay_minutes) return true;
        const windowMin = toMinutes(d.window_end_time) - toMinutes(d.first_fire_at);
        return windowMin > Number(d.window_delay_minutes);
      },
      { message: "Window duration must be greater than the repeat interval", path: ["window_delay_minutes"] }
    )
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

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

function toLocalTime(iso?: string): string {
  // Naive datetime strings from API have no timezone suffix — treat as UTC
  const str = iso && !/Z|[+-]\d{2}:\d{2}$/.test(iso) ? iso + "Z" : iso;
  const d = str ? new Date(str) : new Date();
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function getTimezoneLabel(): string {
  const offset = -new Date().getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const h = Math.floor(Math.abs(offset) / 60).toString().padStart(2, "0");
  const m = (Math.abs(offset) % 60).toString().padStart(2, "0");
  return `GMT${sign}${h}:${m}`;
}

function localHHMMtoUTC(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return `${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}`;
}

function utcHHMMtoLocal(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const d = new Date();
  d.setUTCHours(h, m, 0, 0);
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

function timeToNextOccurrence(timeStr: string): Date {
  const [h, m] = timeStr.split(":").map(Number);
  const next = new Date();
  next.setHours(h, m, 0, 0);
  if (next <= new Date()) next.setDate(next.getDate() + 1);
  return next;
}

const TODAY_DATE: string = new Date().toISOString().slice(0, 10);

const NEW_PIPELINE_DEFAULTS: FormData = {
  name: "",
  source_type: "youtube",
  douyin_cookie_id: null,
  channel_url: "",
  interval_value: 1,
  interval_unit: "days",
  first_fire_at: "",
  fetch_count: 1,
  upload_delay_minutes: 0,
  max_duration_seconds: "" as any,
  caption_template: "{title}",
  caption_ai_prompt: "",
  channel_description: "",
  allow_comment: 1,
  allow_duet: 0,
  allow_stitch: 0,
  visibility_type: 0,
  account_ids: [],
  window_end_time: "",
  window_delay_minutes: "" as any,
  telegram_bot_token: "",
  telegram_chat_id: "",
  clear_bot_token: false,
  sync_from_date: TODAY_DATE,
};

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
    mode: "onSubmit",
    defaultValues: {
      name: "",
      source_type: "youtube" as "youtube" | "douyin",
      douyin_cookie_id: null,
      channel_url: "",
      interval_value: 1,
      interval_unit: "days",
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
      window_end_time: "",
      window_delay_minutes: "" as any,
      telegram_bot_token: "",
      telegram_chat_id: "",
      clear_bot_token: false,
      sync_from_date: TODAY_DATE,
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

      setMultipleRuns(!!pipeline.window_end_time);
      reset({
        name: pipeline.name,
        source_type: (pipeline.source_type ?? "youtube") as "youtube" | "douyin",
        douyin_cookie_id: pipeline.douyin_cookie_id ?? null,
        channel_url: pipeline.channel_url,
        interval_value: pipeline.interval_unit === "hours" ? 1 : pipeline.interval_value,
        interval_unit: (pipeline.interval_unit === "hours" ? "days" : pipeline.interval_unit) as "minutes" | "days",
        first_fire_at: pipeline.interval_unit === "days" && pipeline.first_fire_at
          ? toLocalTime(pipeline.first_fire_at)
          : "",
        fetch_count: pipeline.fetch_count,
        upload_delay_minutes: pipeline.upload_delay_minutes ?? 0,
        max_duration_seconds: pipeline.max_duration_seconds ?? ("" as any),
        caption_template: pipeline.caption_template,
        caption_ai_prompt: pipeline.caption_ai_prompt ?? "",
        channel_description: pipeline.channel_description ?? "",
        allow_comment: uploadOpts.allow_comment as 0 | 1,
        allow_duet: uploadOpts.allow_duet as 0 | 1,
        allow_stitch: uploadOpts.allow_stitch as 0 | 1,
        visibility_type: uploadOpts.visibility_type as 0 | 1,
        account_ids: accountIds,
        window_end_time: pipeline.window_end_time ? utcHHMMtoLocal(pipeline.window_end_time) : "",
        window_delay_minutes: (pipeline.window_delay_minutes ?? "") as any,
        telegram_bot_token: "",
        telegram_chat_id: pipeline.telegram_chat_id ?? "",
        clear_bot_token: false,
        sync_from_date: pipeline.sync_from_date ?? "",
      });
    } else {
      setMultipleRuns(false);
      reset(NEW_PIPELINE_DEFAULTS);
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

  const channelDescriptionValue = watch("channel_description");

  const testPromptMut = useMutation({
    mutationFn: ({ url, prompt }: { url: string; prompt: string }) =>
      Settings.testPrompt(url, prompt, channelDescriptionValue || undefined),
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
    const channelDescription = (data.channel_description || "").trim() || null;
    const windowEndTime = (data.window_end_time || "").trim()
      ? localHHMMtoUTC(data.window_end_time.trim())
      : null;
    const windowDelayMinutes =
      data.window_delay_minutes === "" || data.window_delay_minutes === undefined
        ? null
        : Number(data.window_delay_minutes);
    const douyinCookieId = data.source_type === "douyin" ? (data.douyin_cookie_id ?? null) : undefined;
    const syncFromDate = (data.sync_from_date || "").trim() || null;
    const upload_options = {
      allow_comment: data.allow_comment as 0 | 1,
      allow_duet: data.allow_duet as 0 | 1,
      allow_stitch: data.allow_stitch as 0 | 1,
      visibility_type: data.visibility_type as 0 | 1,
    };

    if (isEdit && pipeline) {
      const payload: PipelineUpdate = {
        name: data.name,
        state: saveState,
        channel_url: data.channel_url,
        douyin_cookie_id: douyinCookieId,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        first_fire_at: firstFireAt,
        caption_template: data.caption_template,
        caption_ai_prompt: aiPrompt,
        channel_description: channelDescription,
        upload_options,
        account_ids: data.account_ids,
        max_duration_seconds: maxDur ?? null,
        upload_delay_minutes: data.upload_delay_minutes,
        window_end_time: windowEndTime,
        window_delay_minutes: windowDelayMinutes,
        telegram_chat_id: data.telegram_chat_id || undefined,
        clear_bot_token: data.clear_bot_token,
        sync_from_date: syncFromDate,
      };
      if (data.telegram_bot_token) {
        payload.telegram_bot_token = data.telegram_bot_token;
      }
      updateMut.mutate({ id: pipeline.id, data: payload });
    } else {
      createMut.mutate({
        name: data.name,
        source_type: data.source_type,
        douyin_cookie_id: douyinCookieId,
        channel_url: data.channel_url,
        fetch_count: data.fetch_count,
        interval_value: data.interval_value,
        interval_unit: data.interval_unit,
        first_fire_at: firstFireAt,
        caption_template: data.caption_template,
        caption_ai_prompt: aiPrompt ?? undefined,
        channel_description: channelDescription ?? undefined,
        upload_options,
        account_ids: data.account_ids,
        max_duration_seconds: maxDur,
        upload_delay_minutes: data.upload_delay_minutes,
        window_end_time: windowEndTime,
        window_delay_minutes: windowDelayMinutes,
        telegram_bot_token: data.telegram_bot_token || undefined,
        telegram_chat_id: data.telegram_chat_id || undefined,
        sync_from_date: syncFromDate,
      });
    }
  }

  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [aiSectionOpen, setAiSectionOpen] = useState(false);
  const [multipleRuns, setMultipleRuns] = useState(false);
  const [testUrl, setTestUrl] = useState("");
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
    const unitLabel = intervalUnit === "minutes" ? "phút" : "ngày";
    return `Pipeline chạy mỗi ${n} ${unitLabel} từ mốc thời gian này khi đang hoạt động.`;
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
            {isEdit ? "Chỉnh sửa pipeline" : "Tạo pipeline mới"}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={20} />
          </button>
        </div>

        <form
          className="space-y-6"
          onSubmit={(e) => e.preventDefault()}
        >
          {/* Section 1: Thông tin cơ bản */}
          <fieldset className="space-y-4">
            <legend className="text-base font-semibold text-slate-800 border-b pb-1 w-full">
              Thông tin cơ bản
            </legend>
            <div>
              <label className="label">Tên pipeline</label>
              <input className="input" {...register("name")} placeholder="Ví dụ: Kênh du lịch" />
              {errors.name && <p className="field-error">{errors.name.message}</p>}
            </div>

            {/* Lịch chạy */}
            <div className="space-y-3">
              <div className="flex gap-3 items-end">
                <div className="flex-1">
                  <label className="label">Lặp lại mỗi</label>
                  <input
                    type="number"
                    className="input"
                    min={1}
                    {...register("interval_value")}
                  />
                </div>
                <div className="flex-1">
                  <label className="label">Đơn vị</label>
                  <select
                    className="input"
                    {...register("interval_unit")}
                    onChange={(e) => {
                      const unit = e.target.value as "minutes" | "days";
                      setValue("interval_unit", unit);
                      setValue("interval_value", unit === "days" ? 1 : 10);
                      if (unit !== "days") {
                        setValue("window_end_time", "");
                        setValue("window_delay_minutes", "" as any);
                        setMultipleRuns(false);
                      }
                    }}
                  >
                    <option value="days">ngày</option>
                    <option value="minutes">phút</option>
                  </select>
                </div>
              </div>
              {errors.interval_value && (
                <p className="field-error">{errors.interval_value.message}</p>
              )}
              {repeatHint && (
                <p className="text-xs text-slate-500">{repeatHint}</p>
              )}

              {intervalUnit === "days" && (
                <div className="space-y-3">
                  <div>
                    <label className="label">Chạy lúc ({getTimezoneLabel()})</label>
                    <input
                      type="time"
                      className="input"
                      {...register("first_fire_at")}
                    />
                    {errors.first_fire_at && (
                      <p className="field-error">{errors.first_fire_at.message}</p>
                    )}
                    <p className="text-xs text-slate-400 mt-1">
                      Nếu giờ này đã qua hôm nay, lần chạy đầu tiên sẽ vào ngày mai.
                    </p>
                  </div>

                  <label className="flex items-center gap-2 text-sm font-medium text-slate-700 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={multipleRuns}
                      onChange={(e) => {
                        setMultipleRuns(e.target.checked);
                        if (!e.target.checked) {
                          setValue("window_end_time", "");
                          setValue("window_delay_minutes", "" as any);
                        }
                      }}
                    />
                    Chạy nhiều lần trong ngày?
                  </label>

                  {multipleRuns && (
                    <div className="space-y-3 pl-4 border-l-2 border-slate-200">
                      <div className="flex gap-3 items-end">
                        <div className="flex-1">
                          <label className="label">Chạy đến lúc ({getTimezoneLabel()})</label>
                          <input
                            type="time"
                            className="input"
                            {...register("window_end_time")}
                          />
                          {errors.window_end_time && (
                            <p className="field-error">{errors.window_end_time.message}</p>
                          )}
                        </div>
                        <div className="flex-1">
                          <label className="label">Lặp lại mỗi (phút)</label>
                          <input
                            type="number"
                            className="input"
                            min={3}
                            placeholder="Ví dụ: 5"
                            {...register("window_delay_minutes")}
                          />
                          {errors.window_delay_minutes && (
                            <p className="field-error">{errors.window_delay_minutes.message}</p>
                          )}
                        </div>
                      </div>
                      <p className="text-xs text-slate-400">
                        Pipeline sẽ chạy lại mỗi N phút từ giờ bắt đầu đến giờ kết thúc, rồi chờ đến ngày hôm sau.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </fieldset>

          {/* Section 2: Tải xuống */}
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold text-slate-800 border-b pb-1 w-full">
              Tải xuống
            </legend>
            {!isEdit && (
              <div>
                <label className="label">Nguồn video</label>
                <select className="input" {...register("source_type")}>
                  <option value="youtube">YouTube</option>
                  <option value="douyin">Douyin</option>
                </select>
              </div>
            )}
            {sourceType === "douyin" && (
              <div>
                <label className="label">Douyin Cookie</label>
                <select
                  className="input"
                  {...register("douyin_cookie_id", { valueAsNumber: true })}
                >
                  <option value="">— Chọn cookie —</option>
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
                    Chưa có cookie Douyin nào. Thêm cookie trong{" "}
                    <Link to="/accounts" className="text-brand-600 hover:underline">
                      Tài khoản → Douyin Cookies
                    </Link>
                    .
                  </p>
                )}
              </div>
            )}
            <div>
              <label className="label">
                {sourceType === "douyin" ? "URL kênh Douyin" : "URL kênh YouTube"}
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
            <div>
              <label className="label">
                Mô tả channel{" "}
                <span className="text-slate-400 font-normal">(tùy chọn — dùng cho AI)</span>
              </label>
              <textarea
                className="input min-h-[60px]"
                maxLength={2000}
                {...register("channel_description")}
                placeholder="Ví dụ: Kênh review xe điện và công nghệ Trung Quốc. Dùng để giúp AI hiểu context khi tiêu đề/mô tả nguồn mơ hồ."
              />
              {errors.channel_description && (
                <p className="field-error">{errors.channel_description.message}</p>
              )}
            </div>
            <div>
              <label className="label">Số video mỗi lần chạy (1–20)</label>
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
                <label className="label">Thời gian chờ giữa các video (phút)</label>
                <input
                  type="number"
                  className="input"
                  min={0}
                  max={30}
                  {...register("upload_delay_minutes")}
                />
                <p className="text-xs text-slate-400 mt-1">
                  Chờ N phút giữa mỗi lần đăng (0 = không chờ). Giúp tránh bị TikTok hạn chế.
                </p>
                {errors.upload_delay_minutes && (
                  <p className="field-error">{errors.upload_delay_minutes.message}</p>
                )}
              </div>
            )}
            <div>
              <label className="label">Độ dài tối đa (giây, tùy chọn)</label>
              <input
                type="number"
                className="input"
                min={1}
                {...register("max_duration_seconds")}
                placeholder="Để trống nếu không giới hạn"
              />
              <p className="text-xs text-slate-400 mt-1">
                Video dài hơn giá trị này sẽ bị bỏ qua hoặc cắt ngắn về N giây đầu tiên.
              </p>
            </div>
            <div>
              <label className="label">Lấy video từ ngày (tùy chọn)</label>
              <input
                type="date"
                className="input"
                {...register("sync_from_date")}
              />
              <p className="text-xs text-slate-400 mt-1">
                Chỉ xử lý video đăng từ ngày này trở đi. Để trống nếu muốn lấy tất cả.
              </p>
            </div>
          </fieldset>

          {/* Section 3: Đăng tải */}
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold text-slate-800 border-b pb-1 w-full">
              Đăng tải
            </legend>

            {/* Accounts */}
            <div className="space-y-2">
              <label className="label">
                Tài khoản TikTok{" "}
                <span className="text-slate-400 font-normal">
                  ({selectedAccounts.length} đã chọn)
                </span>
              </label>
              {validAccounts.length === 0 && (
                <p className="text-xs text-amber-600">
                  Chưa có tài khoản hợp lệ. Hãy nhập cookie trước.
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
            </div>

            {/* Caption */}
            <div>
              <label className="label">Caption</label>
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
                Dùng các biến này trong caption:{" "}
                <code className="bg-slate-100 px-1 rounded">{"{title}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{title_hashtag}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{now}"}</code>,{" "}
                <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code>
              </p>
            </div>

            {/* AI rewrite */}
            <div className="space-y-2">
              <button
                type="button"
                className="flex items-center gap-1 text-sm font-medium text-slate-700"
                onClick={() => setAiSectionOpen(!aiSectionOpen)}
              >
                {aiSectionOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                Viết lại caption bằng AI (tùy chọn)
              </button>
              {aiSectionOpen && (
                <div className="space-y-2 pl-4 border-l-2 border-slate-200">
                  <label className="label">
                    Prompt AI{" "}
                    <button
                      type="button"
                      className="ml-1 text-xs text-brand-600 hover:underline font-normal"
                      onClick={() =>
                        setValue(
                          "caption_ai_prompt",
                          `You are a TikTok caption writer.

Read the video TITLE and DESCRIPTION below, then write an engaging English caption for reposting on TikTok.

Rules:
- Base the caption on BOTH the title and the description — capture what the video is actually about. If a description is given, use it as the main source of detail.
- Stay accurate to the source content; do not invent facts.
- Write natural, engaging English that hooks viewers and encourages watching.
- Keep it concise but complete. The TikTok caption limit is 4000 characters — you do not need to fill it, just be compelling.
- You may add a few relevant hashtags at the end.
- Return only the caption text, with no explanation, labels, or surrounding quotes.`,
                        )
                      }
                    >
                      Dùng mẫu
                    </button>
                  </label>
                  <textarea
                    className="input min-h-[80px]"
                    maxLength={2000}
                    {...register("caption_ai_prompt")}
                    placeholder="Viết lại tiêu đề theo phong cách TikTok hấp dẫn."
                  />
                  {errors.caption_ai_prompt && (
                    <p className="field-error">{errors.caption_ai_prompt.message}</p>
                  )}
                  <div className="space-y-2 border border-slate-200 rounded-md p-3 bg-white">
                    <p className="text-xs font-medium text-slate-600">
                      Thử nghiệm prompt{" "}
                      <span className="font-normal text-slate-400">(chỉ URL YouTube)</span>
                    </p>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        className="input flex-1 text-xs"
                        placeholder="https://www.youtube.com/watch?v=..."
                        value={testUrl}
                        onChange={(e) => setTestUrl(e.target.value)}
                      />
                      <button
                        type="button"
                        className="btn-secondary text-xs whitespace-nowrap"
                        disabled={!testUrl.trim() || !captionAiPrompt?.trim() || testPromptMut.isPending}
                        onClick={() => testPromptMut.mutate({ url: testUrl, prompt: captionAiPrompt || "" })}
                      >
                        {testPromptMut.isPending ? "Đang chạy…" : "Thử nghiệm"}
                      </button>
                    </div>
                    {testPromptMut.data && (
                      <div className="space-y-1 text-xs">
                        <div className="text-slate-500">Tiêu đề gốc: <span className="text-slate-700">{testPromptMut.data.original_title}</span></div>
                        <div className="text-emerald-700 font-medium">AI title: {testPromptMut.data.ai_title}</div>
                        {testPromptMut.data.description_snippet && (
                          <details className="text-slate-400">
                            <summary className="cursor-pointer">Mô tả nguồn</summary>
                            <p className="mt-1 whitespace-pre-wrap">{testPromptMut.data.description_snippet}</p>
                          </details>
                        )}
                      </div>
                    )}
                    {testPromptMut.isError && (
                      <p className="text-xs text-red-600">{(testPromptMut.error as any)?.response?.data?.detail || "Thử nghiệm thất bại"}</p>
                    )}
                  </div>
                  <p className="text-xs text-slate-500">
                    Kết quả được dùng qua biến{" "}
                    <code className="bg-slate-100 px-1 rounded">{"{ai_title}"}</code> trong caption.
                    Cài đặt model mặc định trong{" "}
                    <Link to="/settings" className="text-brand-600 hover:underline">
                      Cài đặt
                    </Link>
                    .
                  </p>
                </div>
              )}
            </div>

            {/* Advanced flags */}
            <div className="space-y-2">
              <button
                type="button"
                className="flex items-center gap-1 text-sm font-medium text-slate-700"
                onClick={() => setAdvancedOpen(!advancedOpen)}
              >
                {advancedOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                Tùy chọn nâng cao
              </button>
              {advancedOpen && (
                <div className="grid grid-cols-2 gap-3 pl-4">
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" value={1} {...register("allow_comment")} defaultChecked />
                    Cho phép bình luận
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" value={1} {...register("allow_duet")} />
                    Cho phép Duet
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" value={1} {...register("allow_stitch")} />
                    Cho phép Stitch
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" value={1} {...register("visibility_type")} />
                    Chế độ riêng tư
                  </label>
                </div>
              )}
            </div>
          </fieldset>

          {/* Section 4: Thông báo */}
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold text-slate-800 border-b pb-1 w-full">
              Thông báo <span className="text-slate-400 font-normal text-sm">(tùy chọn)</span>
            </legend>
            <div className="grid lg:grid-cols-2 gap-3">
              <div>
                <label className="label">Bot Token</label>
                <div className="flex gap-2 items-stretch">
                  <input
                    type="password"
                    className="input flex-1 min-w-0"
                    {...register("telegram_bot_token")}
                    placeholder={
                      isEdit && pipeline?.telegram_bot_token_set
                        ? "••• (đã cấu hình)"
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
                    {telegramDetectLoading ? "Đang tìm…" : "Tự động tìm"}
                  </button>
                </div>
                {isEdit &&
                  pipeline?.telegram_bot_token_set &&
                  !String(telegramBotToken || "").trim() && (
                    <p className="text-xs text-slate-500 mt-1">
                      Nhập lại bot token để sử dụng tính năng này.
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
                    {telegramTestSending ? "Đang gửi…" : "Gửi thử"}
                  </button>
                </div>
                <p className="text-xs text-slate-500 mt-1">
                  Chỉ nhập số (kênh private thường bắt đầu bằng{" "}
                  <code className="bg-slate-100 px-1 rounded">-100…</code>). Thêm bot làm admin kênh.
                </p>
                {errors.telegram_chat_id && (
                  <p className="field-error">{errors.telegram_chat_id.message}</p>
                )}
              </div>
            </div>
            {telegramDetectChats && telegramDetectChats.length > 0 && (
              <div>
                <label className="label">Chọn chat đã tìm thấy</label>
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
                  <option value="">— Chọn —</option>
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
                  Không tìm thấy chat nào. Hãy gửi một tin nhắn cho bot rồi thử lại.
                </p>
              )}
            {telegramDetectErr && (
              <p className="text-sm text-red-600">{telegramDetectErr}</p>
            )}
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
              Hủy
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={isBusy}
              onClick={handleSubmit((d) => onSubmit(d, "paused"))}
            >
              Lưu (tạm dừng)
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white"
              disabled={isBusy}
              onClick={handleSubmit((d) => onSubmit(d, "active"))}
            >
              {isEdit ? "Lưu & kích hoạt" : "Tạo & kích hoạt"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
