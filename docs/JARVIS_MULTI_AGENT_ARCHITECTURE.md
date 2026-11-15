# Jarvis V1 — Autonomous Multi-Agent Workforce Architecture

## 1. Architectural Overview

Jarvis V1 has been transformed from a single-agent assistant into a hierarchical, autonomous multi-agent engineering and operations workforce (the "Avengers" roster). At the center of the architecture is **ATLAS** (Chief of Staff & Team Commander), supported by six specialized autonomous agents: **ORACLE**, **FORGE**, **SENTINEL**, **SCOUT**, **VANGUARD**, and **PULSE**.

```mermaid
flowchart TD
    User([User Voice / React Command Center]) --> Gateway[FastAPI Server & Approval Gateway]
    Gateway --> Atlas[ATLAS: Chief of Staff / Orchestrator]
    
    subgraph MultiAgentWorkforce [Specialist Agent Workforce]
        Atlas -->|DAG Task Delegation| Scout[SCOUT: File & Knowledge]
        Atlas -->|Code & Bug Fixing| Forge[FORGE: Software Engineering]
        Atlas -->|Security Audit| Sentinel[SENTINEL: Security & Infra]
        Atlas -->|Research & Benchmarks| Oracle[ORACLE: Deep Research]
        Atlas -->|QA & Verification| Vanguard[VANGUARD: QA & Verification]
        Atlas -->|Routines & Ops| Pulse[PULSE: Operations & Productivity]
    end

    subgraph CommunicationBus [Inter-Agent Coordination Layer]
        Scout <-->|FINDING / HANDOVER| Forge
        Forge <-->|CODE_REVIEW| Vanguard
        Sentinel <-->|VULNERABILITY| Vanguard
        Oracle <-->|BENCHMARK| Vanguard
        TeamBus[(TeamMessageBus & ConflictResolver)]
    end

    subgraph Persistence [SQLite WAL Storage]
        WorkforceDB[(workforce.db)]
        TaskDB[(tasks.db)]
        ApprovalDB[(approvals.db)]
    end

    MultiAgentWorkforce --> Persistence
    CommunicationBus --> Persistence
    Vanguard -->|Verified Evidence| Atlas
    Atlas -->|Consolidated Executive Report| User
```

---

## 2. Core Architectural Components

### 2.1 ATLAS Orchestrator (`src/openjarvis/workforce/orchestrator.py`)
- **Dynamic Task Planning:** Evaluates incoming user goals and creates dynamic Directed Acyclic Graphs (DAGs) assigning subtasks to domain specialists.
- **Dependency Resolution & Concurrency:** Independent steps execute concurrently; dependent steps block until prerequisite steps pass.
- **Hardware Enveloping:** Parallel worker execution is bounded to `max_workers = 2` to fit within the 4 GB VRAM budget of the NVIDIA GTX 1650.
- **Consolidated Synthesis:** Compiles specialist outputs into structured executive reports with clear findings, actions, and verification certificates.

### 2.2 Agent Workforce Registry (`src/openjarvis/workforce/registry.py`)
- **Singleton Authority:** Tracks the live state, health, active model, and heartbeats of all 7 specialist agents.
- **Dynamic Tool Registration:** Imperatively resolves missing tools (`think`, `knowledge_search`, `n8n`) upon server startup or test fixtures.
- **Stale Agent Sweeper:** Background daemon marks agents `DEGRADED` if heartbeats lapse beyond 120 seconds.
- **Natural Language Status Queries:** Intercepts conversational questions ("How many agents are online?", "What is Forge working on?") and provides live presence answers.
- **Startup Announcement:** Generates spoken readiness summaries respecting user quiet hours (`Asia/Kolkata`).

### 2.3 Team Communication & Conflict Resolution (`src/openjarvis/workforce/communication.py`)
- **Inter-Agent Message Bus:** Standardized messaging types (`FINDING`, `CONTEXT_REQUEST`, `VERIFICATION_REQUEST`, `BLOCKER`, `HANDOVER`).
- **Conflict Resolution Engine:** Automatically detects contradictions between specialists (e.g., discrepancies in bug counts or risk levels) and enforces verification against source evidence.
- **Scoped Context Filtering:** Sanitizes handover context between agents to prevent token bloat and hallucination.

### 2.4 Persistent Task State (`src/openjarvis/tasks/engine.py` & `store.py`)
- **10-State Task State Machine:** (`CREATED`, `PLANNING`, `RUNNING`, `WAITING_APPROVAL`, `PAUSED`, `RECOVERING`, `VERIFYING`, `COMPLETED`, `FAILED`, `CANCELLED`).
- **Crash Recovery:** Interrupted tasks resume from the last completed checkpoint on startup without duplicate side effects.
- **Steering Controls:** Users can dynamically steer active tasks or skip failing steps.

---

## 3. Hardware Boundary Protection (GTX 1650 4 GB VRAM)

| Component | Target Device | Allocation Policy |
| :--- | :--- | :--- |
| **Active LLM Workers** | NVIDIA GTX 1650 | Max 2 concurrent completions; quantized 4-bit / 8-bit models |
| **Speech Pipeline (Whisper/Kokoro)** | AMD Ryzen 5 5600H CPU | Offloaded to CPU to prevent GPU VRAM exhaustion |
| **Routine & Background Scheduling** | System Daemon Threads | Memory footprint < 30 MB; zero GPU allocation |
| **Database Engines** | SQLite WAL | Direct SSD file I/O |

---

## 4. Source Files Map

- **Workforce Models:** [src/openjarvis/workforce/models.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/models.py)
- **Workforce Definitions:** [src/openjarvis/workforce/definitions.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/definitions.py)
- **Workforce Persistence:** [src/openjarvis/workforce/store.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/store.py)
- **Workforce Registry:** [src/openjarvis/workforce/registry.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/registry.py)
- **Atlas Orchestrator:** [src/openjarvis/workforce/orchestrator.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/orchestrator.py)
- **Team Communication:** [src/openjarvis/workforce/communication.py](file:///d:/projects/oprn-jarvis/src/openjarvis/workforce/communication.py)
- **Workforce API Endpoints:** [src/openjarvis/server/workforce_routes.py](file:///d:/projects/oprn-jarvis/src/openjarvis/server/workforce_routes.py)
- **React Frontend:** [frontend/src/components/Desktop/AgentWorkforce.tsx](file:///d:/projects/oprn-jarvis/frontend/src/components/Desktop/AgentWorkforce.tsx)
