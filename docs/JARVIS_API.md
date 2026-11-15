# OpenJarvis REST API Reference (Mark-LVII)

Base URL: `http://127.0.0.1:8000` (or configured host/port)  
Authentication: Optional local bearer token or session cookie.

---

## 1. System & Runtime Capabilities

### `GET /v1/capabilities`
Returns runtime host hardware detection, tool registry counts, speech subsystem state, and active security policies.

**Response `200 OK`**:
```json
{
  "system": "OpenJarvis",
  "version": "1.1.0-mark-lvii",
  "hardware": {
    "os": "Windows-11-10.0.26300-SP0",
    "cpu_name": "AMD Ryzen 5 5600H with Radeon Graphics",
    "cpu_logical_cores": 12,
    "ram_total_gb": 15.35,
    "ram_available_gb": 8.12,
    "gpu_name": "NVIDIA GeForce GTX 1650",
    "gpu_vram_total_mb": 4096,
    "gpu_vram_free_mb": 3248,
    "gpu_available": true
  },
  "tools": {
    "count": 25,
    "list": ["code_exec", "file_read", "file_write", "shell_exec", ...]
  },
  "speech": {
    "supported": true,
    "wake_word_supported": true,
    "wake_phrase": "Hey Jarvis",
    "wake_active": false,
    "session_state": "idle",
    "stt_engine": "faster-whisper",
    "tts_engine": "kokoro",
    "echo_guard": true
  },
  "security": {
    "permission_tiers": ["read_only", "safe_local", "reversible_local", "sensitive", "irreversible"],
    "approval_gateway_enforced": true,
    "fresh_approval_required_for_sensitive": true,
    "action_journal_enabled": true,
    "file_compensation_undo": true
  }
}
```

---

## 2. Autonomous Background Tasks

### `GET /v1/tasks`
List background tasks with optional status filtering.
- Query parameters:
  - `status` (string, optional): Filter by `created`, `planning`, `running`, `waiting_approval`, `paused`, `completed`, `failed`.
  - `limit` (int, default 50)

### `POST /v1/tasks`
Create a new background task.
- Body:
```json
{
  "goal": "Research lightweight local embedding models for 4GB VRAM",
  "capability_profile": "research",
  "budget": {
    "max_steps": 10,
    "timeout_seconds": 600,
    "max_tool_calls": 25
  }
}
```

### `GET /v1/tasks/{task_id}`
Retrieve task details including step progress, current state, and checkpoints.

### `POST /v1/tasks/{task_id}/start`
Initiate background execution for a newly created task.

### `POST /v1/tasks/{task_id}/pause`
Pause a running task immediately.

### `POST /v1/tasks/{task_id}/resume`
Resume execution of a paused task from its last recorded checkpoint.

### `POST /v1/tasks/{task_id}/cancel`
Cancel task execution permanently.

### `POST /v1/tasks/{task_id}/steer`
Inject guidance or instructions into a running/paused task loop.
- Body: `{"guidance": "Focus on GGUF format and compare latency"}`

### `POST /v1/tasks/{task_id}/skip`
Skip the current step and proceed to the next step in the execution plan.

### `GET /v1/tasks/{task_id}/events`
Retrieve full audit stream of events emitted during task execution.

### `GET /v1/tasks/{task_id}/artifacts`
Retrieve artifacts (e.g. Markdown reports, exported files) created by the task.

---

## 3. Unified Routines & Scheduling

### `GET /v1/routines`
List all scheduled routines and their execution status.

### `POST /v1/routines`
Create a new scheduled routine.
- Body:
```json
{
  "name": "Daily Morning Briefing",
  "routine_type": "daily",
  "cron_expr": "0 8 * * *",
  "action_type": "summary_brief",
  "quiet_hours_behavior": "suppress_voice",
  "payload": {"calendar": true, "weather": true}
}
```

### `POST /v1/routines/{id}/pause`
Pause a scheduled routine.

### `POST /v1/routines/{id}/resume`
Resume a scheduled routine.

### `POST /v1/routines/{id}/run`
Trigger immediate execution of a routine out-of-schedule.

### `DELETE /v1/routines/{id}`
Delete a routine.

### `GET /v1/routines/quiet-hours`
Get current quiet-hours configuration and status.
- Response:
```json
{
  "timezone": "Asia/Kolkata",
  "start_time": "22:00",
  "end_time": "07:00",
  "is_quiet_hours": false,
  "queued_notifications": 0
}
```

---

## 4. Action Security, Approvals & Action Journal

### `GET /v1/approvals/pending`
List all pending approval requests awaiting human authorization.

### `POST /v1/approvals/{action_id}/approve`
Approve an action. Note: Sensitive operations (`TIER_HIGH`) require fresh explicit approval every time; `remember=true` is ignored for high-risk actions.

### `POST /v1/approvals/{action_id}/deny`
Deny an action request.

### `GET /v1/security/actions`
List privileged actions recorded in the SQLite Action Journal.
- Query parameters: `limit`, `tool_name`, `status`.

### `GET /v1/security/actions/{action_id}`
Retrieve detailed action record including parameters, target, backup snapshot path, and reversibility.

### `POST /v1/security/actions/{action_id}/undo`
Rollback or compensate an executed action:
- Restores modified file from pre-modification backup snapshot.
- Deletes file created by the action.
- Marks action as `undone`.

---

## 5. Categorized Memory & Facts

### `GET /v1/memory/facts`
Query stored facts with optional filtering.
- Query parameters:
  - `category` (string, optional): One of `preferences`, `projects`, `environment`, `tools`, `decisions`, `tasks`, `goals`, `facts`.
  - `query` (string, optional): Text search query.
  - `limit` (int, default 100).

### `POST /v1/memory/facts`
Store a new categorized memory fact.
- Body:
```json
{
  "text": "User prefers high contrast dark theme",
  "category": "preferences",
  "confidence": 1.0,
  "pinned": true
}
```

### `PUT /v1/memory/facts/{fact_id}`
Update fact text, category, or pinned status.

### `DELETE /v1/memory/facts/{fact_id}`
Delete a memory fact.

### `POST /v1/memory/facts/{fact_id}/pin`
Pin or unpin a memory fact.
- Body: `{"pinned": true}`

---

## 6. Voice & Speech Intelligence

### `GET /v1/speech/devices`
List host audio input and output devices.

### `GET /v1/speech/state`
Get current voice session state (`idle`, `wake_listening`, `active_listening`, `processing`, `speaking`, `interrupted`).

### `POST /v1/speech/interrupt`
Trigger immediate audio playback interruption (barge-in).

### `POST /v1/speech/wake/start`
Start local wake-word detection loop ("Hey Jarvis").

### `POST /v1/speech/wake/stop`
Stop local wake-word detection loop.

### `POST /v1/speech/transcribe`
Transcribe audio file using Faster-Whisper.

### `POST /v1/speech/synthesize`
Synthesize speech audio using Kokoro TTS with language and voice options.

---

## 7. Model Router

### `GET /v1/models`
List available models, currently active model, and host VRAM limits.

### `GET /v1/models/current`
Get currently selected inference model.

### `POST /v1/models/switch`
Switch active model with 4GB VRAM safety validation.
- Body: `{"model": "qwen2.5:3b"}`
- Response includes warnings if selected model exceeds 4GB local VRAM limits.
