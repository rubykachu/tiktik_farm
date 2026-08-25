# farm_tiktik v2

Automation platform that pulls videos from source channels (YouTube, Douyin) and re-uploads them to TikTok accounts on a recurring schedule, or hands them to a human to post by hand.

## Language

### Core Entities

**Pipeline**:
A recurring automation configuration that pairs a source channel with an upload schedule and, optionally, TikTok accounts. Executing a pipeline once is a **tick**.
_Avoid_: Job, task, schedule, workflow

**Download-only pipeline**:
A pipeline with no TikTok account. It downloads and notifies, uploads nothing.
_Avoid_: Accountless pipeline, notify-only pipeline

**Pipeline tick**:
One execution cycle of a pipeline — fetch videos from source, dedupe, upload to accounts (if any), notify, record outcomes. Triggered by the scheduler or manually.
_Avoid_: Run (ambiguous — use "tick" for the action, "pipeline run" for the record)

**Pipeline run**:
The persisted record of a single tick: counts, summary text, error log, and timing.
_Avoid_: Execution, job run

**Video upload**:
The persisted record of one (video, account) upload attempt within a pipeline run. The dedup key.
_Avoid_: Upload attempt, upload record

**Account**:
A TikTok username with a stored session cookie. Distinct from login credentials.
_Avoid_: TikTok account, user

### Source

**Source type**:
The origin platform of a pipeline's videos: `youtube` or `douyin`.

**Aweme ID**:
Douyin's internal identifier for a post/video. The Douyin-side dedup key.
_Avoid_: Douyin video ID, post ID

**Fetch count**:
The maximum number of newest videos to pull from the source channel per tick (1–20).
_Avoid_: Limit, batch size

### Scheduling

**Interval**:
The repeat frequency of a pipeline, expressed as a value + unit (minutes / hours / days).

**Window mode**:
Optional scheduling variant where a pipeline fires multiple times within a daily time window (e.g., 08:00–12:00 every 30 min), rather than on a fixed interval from last run.

**Lock**:
A soft TTL claimed by the worker before executing a tick. Prevents concurrent execution of the same pipeline. Renewed via heartbeat during execution.
_Avoid_: Mutex, semaphore

**Orphan reaper**:
Startup process that finalizes `PipelineRun` records that never completed (started >15 min ago with no finish time), typically from a prior crash.
_Avoid_: Cleanup job, stale run fixer

### Deduplication

**Dedupe**:
The check that skips a video already handled. Key: `(account_id, video_id)` on `VideoUpload`. Videos with ≥3 failures are also skipped.
_Avoid_: Duplicate check, deduplication

**Seen video**:
The dedupe key a download-only pipeline uses instead, since it writes no `VideoUpload` row: `(pipeline_id, source_key)` on `PipelineSeenVideo`. Kept for the life of the pipeline — an old video still in the channel's top-N must stay deduped.
_Avoid_: Seen record, history row

**Skipped (dup)**:
A video skipped because it was previously uploaded successfully to the account.

**Skipped (exhausted)**:
A video skipped because it failed ≥3 upload attempts to the account.

### Captions

**Caption template**:
A template string with named variables (`{title}`, `{now}`, `{ai_title}`, etc.) rendered per upload to produce the final caption.
_Avoid_: Caption format, post text

**Rendered caption**:
The final caption text after all template variables are substituted. Stored on each video upload for audit.
_Avoid_: Final caption, resolved caption

**AI title**:
An LLM-rewritten version of the source title, used when `{ai_title}` appears in the caption template. Falls back to source title if LLM fails.
_Avoid_: AI caption, rewritten title

### Notifications

**Telegram bot token**:
The Bot API credential for a pipeline's notification channel (format: `digits:alphanumeric`). Stored encrypted; never returned in GET responses.

**Telegram chat ID**:
The destination Telegram chat, group, or channel for pipeline notifications (numeric).

**Download link**:
A signed URL to one downloaded video, included in every notification. Its HMAC proves the server issued it and never expires; the file's 24h retention is what bounds the link's usefulness. This is the only delivery that works past Telegram's 50 MB bot-upload cap.
_Avoid_: Public link, share link, signed token

**Video attachment**:
The optional extra of also uploading the file into the chat (`telegram_attach_video`). Capped at 50 MB by Telegram.
_Avoid_: Media upload, file send

**Attach fields**:
Account fields — currently login and password — copied into the notification so the operator can sign in and post by hand.
_Avoid_: Credentials block, account info

### Manual downloads

**Manual download**:
One pasted source URL fetched for the operator to post by hand, outside any pipeline. Kept 24h, then purged with its file.
_Avoid_: Ad-hoc download, one-off job

**Origin**:
Which path put a file in the download store: `manual` (pasted URL) or `pipeline` (retained so a notification's download link resolves).
_Avoid_: Source (already means the origin platform), kind

### Triggers

**Trigger reason**:
The origin of a pipeline tick: `scheduled` (timer-driven) or `manual` (run-now button).

**Run-now**:
A manual trigger that executes a pipeline immediately without advancing `next_fire_at`.
_Avoid_: Manual run, instant trigger
