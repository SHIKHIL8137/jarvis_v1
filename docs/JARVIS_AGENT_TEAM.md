# Jarvis V1 — Specialist Agent Workforce Roster

The Jarvis multi-agent workforce operates as an organized engineering and operations team inspired by the Avengers roster.

---

## 1. Specialist Roster Summary

| Agent | Roster Title | Role & Focus | Model Preference | Allowed Tools | Explicitly Denied Tools |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ATLAS** | Chief of Staff | Team Commander, DAG Planner & Consolidated Reporter | `deepseek-chat` | `think`, `knowledge_search`, `memory_manage` | `shell_exec`, `file_write`, `desktop` |
| **ORACLE** | Research Specialist | Deep Web & Academic Research, Evidence Extraction | `gpt-4o` | `web_search`, `knowledge_search`, `retrieval`, `think`, `scan_chunks`, `calculator`, `file_read` | `shell_exec`, `file_write`, `desktop`, `git_tool`, `channel_send` |
| **FORGE** | Software Engineering | Codebase Inspection, Architecture, Bug Fixing, Refactoring | `deepseek-coder` | `file_read`, `file_write`, `git_tool`, `think`, `shell_exec`, `knowledge_search` | `desktop`, `channel_send` |
| **SENTINEL** | Security & Infrastructure | Vulnerability Scans, Dependency Audits, Risk Analysis | `claude-3-5-sonnet` | `file_read`, `knowledge_search`, `think`, `scan_chunks` | `shell_exec`, `file_write`, `desktop`, `git_tool`, `channel_send` |
| **SCOUT** | File & Knowledge | Repository File Search, Memory & Schema Indexing | `deepseek-chat` | `file_read`, `knowledge_search`, `scan_chunks`, `retrieval`, `memory_manage`, `think` | `shell_exec`, `file_write`, `desktop`, `git_tool`, `channel_send` |
| **VANGUARD** | QA & Verification | Acceptance Criteria Testing, Evidence Validation | `gpt-4o` | `think`, `file_read`, `knowledge_search`, `shell_exec` | `file_write`, `desktop`, `channel_send` |
| **PULSE** | Operations & Productivity | Routine Engine, Background Tasks, System Health | `deepseek-chat` | `think`, `knowledge_search`, `file_read`, `n8n` | `shell_exec`, `file_write`, `desktop` |

---

## 2. Agent Detailed Specifications

### 2.1 ATLAS — Chief of Staff / Coordinator
- **Manifest ID:** `atlas`
- **Icon:** 🏛️
- **Primary Responsibility:** Deconstructs complex user prompts into DAG execution plans. Assigns steps to domain specialists, manages inter-agent dependencies, monitors execution status, and synthesizes consolidated mission reports.
- **Security Boundaries:** Cannot directly mutate local files or execute arbitrary shell commands. Relies on Forge and Vanguard for system modifications.

### 2.2 ORACLE — Research Specialist
- **Manifest ID:** `oracle`
- **Icon:** 🔮
- **Primary Responsibility:** Executes multi-phase research pipelines (Query Expansion, Parallel Search, Content Extraction, Citation Verification, Synthesis). Provides authoritative comparisons, benchmarks, and external intelligence.
- **Security Boundaries:** Strictly read-only. Denied all write, shell, desktop, and messaging tools.

### 2.3 FORGE — Software Engineering Specialist
- **Manifest ID:** `forge`
- **Icon:** 🔨
- **Primary Responsibility:** Full-stack development, code refactoring, bug investigation, and test running. Operates in permitted project workspaces.
- **Security Boundaries:** Any file modification or git push requires fresh explicit approval through the Approval Gateway before write execution.

### 2.4 SENTINEL — Security & Infrastructure Specialist
- **Manifest ID:** `sentinel`
- **Icon:** 🛡️
- **Primary Responsibility:** Performs vulnerability scans, reviews package dependencies (`requirements.txt`, `package.json`), audits configuration files for leaked secrets, and flags security risks.
- **Security Boundaries:** Read-only analysis. Cannot approve its own security mitigations or execute destructive commands.

### 2.5 SCOUT — File & Knowledge Specialist
- **Manifest ID:** `scout`
- **Icon:** 🧭
- **Primary Responsibility:** Rapid codebase exploration, repository indexing, AST and symbol search, memory retrieval, and file structure mapping.
- **Security Boundaries:** Read-only filesystem exploration. Denied tool mutations.

### 2.6 VANGUARD — QA & Verification Specialist
- **Manifest ID:** `vanguard`
- **Icon:** ⚖️
- **Primary Responsibility:** Runs test suites, validates specialist claims against raw evidence, inspects edge cases, and verifies that results fulfill user criteria before Atlas reports success.
- **Security Boundaries:** Permitted to run read-only test commands via `shell_exec`, but cannot perform unapproved write operations.

### 2.7 PULSE — Operations & Productivity Specialist
- **Manifest ID:** `pulse`
- **Icon:** ⚡
- **Primary Responsibility:** Coordinates scheduled routines, calendar reminders, system telemetry checks, and automated webhook workflows via n8n.
- **Security Boundaries:** Suppresses voice output during configured quiet hours (`Asia/Kolkata`).
