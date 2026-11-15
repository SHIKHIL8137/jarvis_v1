# Jarvis V1 — Multi-Agent Workforce Test Suite Documentation

## 1. Overview

The multi-agent workforce features a comprehensive, automated test suite covering unit, integration, security, and end-to-end acceptance scenarios. All tests run under `pytest` with zero mocks for core business logic and verified SQLite WAL storage.

---

## 2. Test Matrix

| Test Module | Test Name | Target Functionality | Status |
| :--- | :--- | :--- | :--- |
| `tests/workforce/test_agent_registry.py` | `test_workforce_registry_initialization` | Verifies loading of 7 specialist manifests, tool validation, and IDLE transition. | **PASSED** |
| | `test_heartbeat_recording_and_stale_detection` | Verifies live heartbeat updates and degraded transition when stale. | **PASSED** |
| | `test_natural_language_status_queries` | Verifies conversational status answers ("How many online?", "What is Forge doing?"). | **PASSED** |
| | `test_startup_announcement_generation` | Verifies spoken initialization text formatting. | **PASSED** |
| `tests/workforce/test_atlas_orchestrator.py` | `test_heuristic_planner_dag_generation` | Verifies DAG decomposition for ERP review into 5 steps with dependencies. | **PASSED** |
| | `test_dag_validation_and_acyclicity` | Verifies cycle detection and dependency sanitization. | **PASSED** |
| | `test_team_messaging_and_conflict_resolution` | Verifies `TeamMessageBus` and `ConflictResolver` contradiction handling. | **PASSED** |
| `tests/workforce/test_workforce_routes.py` | `test_list_agents_and_summary` | Verifies `GET /v1/workforce/agents` and `/summary`. | **PASSED** |
| | `test_agent_detail_enable_disable` | Verifies enable/disable state transitions via REST API. | **PASSED** |
| | `test_heartbeat_and_nl_query` | Verifies posting heartbeats and querying status over HTTP. | **PASSED** |
| | `test_delegate_team_task` | Verifies task delegation, DAG step generation, and timeline API. | **PASSED** |
| `tests/workforce/test_workforce_security.py` | `test_agent_tool_denial_enforcement` | Verifies Oracle cannot execute denied `file_write` or `shell_exec`. | **PASSED** |
| | `test_sensitive_action_requires_fresh_human_approval` | Verifies Forge `file_write` is halted in `WAITING_APPROVAL` until explicit approval. | **PASSED** |
| | `test_self_approval_bypass_prevention` | Verifies that AI parameters claiming approval are rejected. | **PASSED** |
| | `test_vram_bounded_parallel_workers` | Verifies bounded parallelism (`max_workers = 2`) for GTX 1650 envelope. | **PASSED** |
| `tests/workforce/test_workforce_e2e.py` | `test_erp_analysis_multi_agent_e2e` | End-to-end acceptance test: Scout -> Forge -> Sentinel -> Oracle -> Vanguard -> Atlas. | **PASSED** |

---

## 3. Running the Test Suite

Execute the entire workforce test suite using the project virtual environment:

```powershell
.venv\Scripts\python.exe -m pytest tests/workforce/ -v
```

All 16 tests execute and pass in under 7 seconds.
