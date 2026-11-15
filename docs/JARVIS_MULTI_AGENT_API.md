# Jarvis V1 — Agent Workforce REST API Reference

All workforce endpoints are mounted under `/v1/workforce` and serve real-time state from `AgentWorkforceRegistry` and `WorkforceStore`.

---

## 1. Agent Registry Endpoints

### `GET /v1/workforce/agents`
Returns list of all registered agents with real-time runtime states, model preferences, and heartbeats.

**Response:**
```json
{
  "agents": [
    {
      "id": "atlas",
      "display_name": "Atlas",
      "role": "Chief of Staff / Coordinator",
      "description": "Coordinates the multi-agent workforce, creates execution plans...",
      "avatar": "🏛️",
      "enabled": true,
      "status": "idle",
      "active_model": "deepseek-chat",
      "current_task_id": null,
      "runtime_health": "healthy",
      "last_heartbeat": 1700000000.0,
      "allowed_tools": ["think", "knowledge_search", "memory_manage"],
      "denied_tools": ["shell_exec", "file_write", "desktop"],
      "version": "1.0.0"
    }
  ]
}
```

### `GET /v1/workforce/agents/{agent_id}`
Returns full manifest and status for a single agent.

### `POST /v1/workforce/agents/{agent_id}/enable`
Enables an agent and transitions it to `IDLE` / `ONLINE`.

### `POST /v1/workforce/agents/{agent_id}/disable`
Disables an agent and transitions it to `OFFLINE`.

---

## 2. Heartbeat & Presence Endpoints

### `POST /v1/workforce/agents/{agent_id}/heartbeat`
Records a live heartbeat from an active specialist worker.

**Request Body:**
```json
{
  "current_task_id": "task-123",
  "active_model": "deepseek-coder",
  "error": null
}
```

### `GET /v1/workforce/summary`
Returns team presence summary metrics.

**Response:**
```json
{
  "total_registered": 7,
  "total_online": 7,
  "total_idle": 6,
  "total_busy": 1,
  "total_waiting_approval": 0,
  "total_degraded": 0,
  "total_offline": 0,
  "total_failed": 0,
  "active_tasks": 1
}
```

---

## 3. Conversational Presence & Announcements

### `POST /v1/workforce/query`
Answers natural language inquiries regarding agent presence or workload.

**Request Body:**
```json
{
  "query": "How many agents are online?"
}
```

**Response:**
```json
{
  "query": "How many agents are online?",
  "answer": "Sir, 7 agents are registered. 7 are currently online (6 idle, 1 busy with tasks). 0 offline."
}
```

### `GET /v1/workforce/announcement`
Returns the spoken startup announcement text.

---

## 4. Multi-Agent Task Delegation

### `POST /v1/workforce/tasks`
Delegates a high-level goal to ATLAS, which plans a DAG and initiates execution via `BackgroundTaskEngine`.

**Request Body:**
```json
{
  "goal": "Analyze ERP project, identify bugs, check security, and recommend improvements",
  "priority": "normal"
}
```

**Response:**
```json
{
  "status": "delegated",
  "task_id": "task-uuid-here",
  "goal": "Analyze ERP project...",
  "steps": [
    {
      "step_id": "step-1",
      "step_number": 1,
      "title": "Repository & Context Discovery",
      "assigned_agent": "scout",
      "dependencies": [],
      "tool_name": "file_read"
    }
  ]
}
```

### `GET /v1/workforce/tasks/{task_id}/timeline`
Returns inter-agent messages and specialist reports for a task.

### `GET /v1/workforce/reports`
Lists structured specialist reports, with optional `task_id` and `agent_id` filters.
