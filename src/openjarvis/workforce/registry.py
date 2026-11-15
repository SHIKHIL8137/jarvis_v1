"""Agent Workforce Registry — real presence, heartbeats, health checks, and natural-language status queries."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.events import EventBus
from openjarvis.core.registry import ToolRegistry
from openjarvis.workforce.definitions import INITIAL_WORKFORCE, load_manifests_from_disk
from openjarvis.workforce.models import (
    AgentHeartbeat,
    AgentManifest,
    AgentRuntimeState,
    TeamSummary,
)
from openjarvis.workforce.store import WorkforceStore

logger = logging.getLogger(__name__)

_GLOBAL_WORKFORCE_REGISTRY: Optional[AgentWorkforceRegistry] = None


def _ensure_tools_registered() -> None:
    """Ensure core built-in tools are registered in ToolRegistry."""
    from openjarvis.core.registry import ToolRegistry

    tools_to_check = {
        "think": ("openjarvis.tools.think", "ThinkTool"),
        "file_read": ("openjarvis.tools.file_read", "FileReadTool"),
        "file_write": ("openjarvis.tools.file_write", "FileWriteTool"),
        "shell_exec": ("openjarvis.tools.shell_exec", "ShellExecTool"),
        "knowledge_search": ("openjarvis.tools.knowledge_search", "KnowledgeSearchTool"),
        "git_status": ("openjarvis.tools.git_tool", "GitStatusTool"),
        "git_diff": ("openjarvis.tools.git_tool", "GitDiffTool"),
    }
    for tool_name, (mod_path, cls_name) in tools_to_check.items():
        if not ToolRegistry.contains(tool_name):
            try:
                import importlib

                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name, None)
                if cls and not ToolRegistry.contains(tool_name):
                    ToolRegistry.register_value(tool_name, cls)
            except Exception as exc:
                logger.debug("Could not register %s: %s", tool_name, exc)


class AgentWorkforceRegistry:
    """Central authority for agent workforce registration, live runtime states,
    heartbeats, health checking, and presence reporting."""

    def __init__(
        self,
        store: Optional[WorkforceStore] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.store = store or WorkforceStore()
        self.bus = bus
        self._manifests: Dict[str, AgentManifest] = load_manifests_from_disk()
        self._states: Dict[str, AgentRuntimeState] = {aid: AgentRuntimeState.IDLE for aid in self._manifests}
        self._heartbeats: Dict[str, AgentHeartbeat] = {}
        self._active_tasks: Dict[str, str] = {}  # agent_id -> task_id
        self._lock = threading.RLock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()
        self._initialized = False

        global _GLOBAL_WORKFORCE_REGISTRY
        _GLOBAL_WORKFORCE_REGISTRY = self

    @classmethod
    def get_instance(
        cls,
        store: Optional[WorkforceStore] = None,
        bus: Optional[EventBus] = None,
    ) -> AgentWorkforceRegistry:
        return get_workforce_registry(store=store, bus=bus)

    def initialize_workforce(self, engine: Optional[Any] = None) -> None:
        """Initialize all enabled specialist agents at startup."""
        with self._lock:
            _ensure_tools_registered()

            # 1. Load manifests
            self._manifests = load_manifests_from_disk()
            for m in self._manifests.values():
                self.store.save_manifest(m)

            # 2. Initialize runtime states & run health checks
            for agent_id, manifest in self._manifests.items():
                if not manifest.enabled:
                    self._states[agent_id] = AgentRuntimeState.OFFLINE
                    self.store.update_runtime_status(agent_id, AgentRuntimeState.OFFLINE)
                    continue

                self._states[agent_id] = AgentRuntimeState.INITIALIZING
                self.store.update_runtime_status(agent_id, AgentRuntimeState.INITIALIZING)

                # Health check tools & models
                healthy, reason = self._check_agent_health(manifest, engine)
                final_state = (
                    AgentRuntimeState.IDLE if healthy else AgentRuntimeState.DEGRADED
                )
                self._states[agent_id] = final_state

                # Dynamic model allocation based on agent capabilities
                try:
                    from openjarvis.models import get_model_selection_engine

                    sel_engine = get_model_selection_engine()
                    sel = sel_engine.select_model(
                        task_type=manifest.id,
                        required_capabilities=manifest.required_capabilities,
                        local_only=manifest.local_only,
                        preferred_model=manifest.model_preference,
                        fallback_model=manifest.model_fallback,
                        agent_id=manifest.id,
                    )
                    active_model = sel.model_id
                except Exception as exc:
                    logger.debug("Dynamic model selection for %s fell back: %s", agent_id, exc)
                    active_model = manifest.model_preference or "discovered-auto"

                hb = AgentHeartbeat(
                    agent_id=agent_id,
                    status=final_state,
                    timestamp=time.time(),
                    active_model=active_model,
                    runtime_health="healthy" if healthy else "degraded",
                    last_error=None if healthy else reason,
                    available_tools=self._resolve_tools(manifest),
                )
                self._heartbeats[agent_id] = hb
                self.store.update_runtime_status(
                    agent_id=agent_id,
                    status=final_state,
                    active_model=active_model,
                    runtime_health=hb.runtime_health,
                    last_error=hb.last_error,
                )

            self._initialized = True
            self.store.log_team_event(
                "workforce_initialized",
                {"total_agents": len(self._manifests), "online": self.count_online()},
            )
            logger.info(
                "AgentWorkforceRegistry: Initialized %d agents (%d online)",
                len(self._manifests),
                self.count_online(),
            )

    def _resolve_tools(self, manifest: AgentManifest) -> List[str]:
        """Resolve available tools for an agent filtering out denied tools."""
        registered = set(ToolRegistry.keys())
        if manifest.allowed_tools:
            available = set(manifest.allowed_tools).intersection(registered)
        else:
            available = registered
        available = available - set(manifest.denied_tools)
        return sorted(list(available))

    def _check_agent_health(
        self, manifest: AgentManifest, engine: Optional[Any]
    ) -> Tuple[bool, Optional[str]]:
        """Validate agent tools and capability readiness."""
        # Check required tools
        missing_tools = [
            t for t in manifest.allowed_tools if not ToolRegistry.contains(t)
        ]
        if missing_tools:
            logger.warning(
                "Agent %s has missing tools: %s", manifest.id, missing_tools
            )
            # Soft degradation instead of hard fail
            return False, f"Missing tools: {', '.join(missing_tools)}"

        return True, None

    # --- Heartbeat & Presence Loop ---

    def start_heartbeat_monitor(self, interval_seconds: float = 15.0) -> None:
        """Start background heartbeat & stale-agent detection daemon."""
        with self._lock:
            if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                return
            self._stop_heartbeat.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(interval_seconds,),
                name="agent-heartbeat-monitor",
                daemon=True,
            )
            self._heartbeat_thread.start()

    def stop_heartbeat_monitor(self) -> None:
        self._stop_heartbeat.set()
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=2.0)

    def _heartbeat_loop(self, interval: float) -> None:
        while not self._stop_heartbeat.is_set():
            time.sleep(interval)
            try:
                self._sweep_stale_agents()
            except Exception as exc:
                logger.debug("Heartbeat sweep error: %s", exc)

    def _sweep_stale_agents(self, stale_timeout_seconds: float = 120.0) -> None:
        """Detect agents that haven't heartbeated recently and mark degraded/offline."""
        with self._lock:
            now = time.time()
            for agent_id, hb in self._heartbeats.items():
                if self._states.get(agent_id) in (
                    AgentRuntimeState.OFFLINE,
                    AgentRuntimeState.FAILED,
                ):
                    continue
                if (now - hb.timestamp) > stale_timeout_seconds:
                    logger.warning(
                        "Agent %s heartbeat stale (last: %.1fs ago). Marking DEGRADED.",
                        agent_id,
                        now - hb.timestamp,
                    )
                    self._states[agent_id] = AgentRuntimeState.DEGRADED
                    hb.runtime_health = "degraded"
                    hb.last_error = "Heartbeat timed out"
                    self.store.update_runtime_status(
                        agent_id,
                        AgentRuntimeState.DEGRADED,
                        runtime_health="degraded",
                        last_error=hb.last_error,
                    )

    def record_heartbeat(
        self,
        agent_id: str,
        current_task_id: Optional[str] = None,
        active_model: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record live heartbeat from active worker."""
        with self._lock:
            if agent_id not in self._manifests:
                return

            now = time.time()
            current_state = self._states.get(agent_id, AgentRuntimeState.IDLE)
            if current_task_id:
                current_state = AgentRuntimeState.BUSY
                self._active_tasks[agent_id] = current_task_id
            elif current_state == AgentRuntimeState.BUSY:
                current_state = AgentRuntimeState.IDLE
                self._active_tasks.pop(agent_id, None)

            self._states[agent_id] = current_state

            hb = AgentHeartbeat(
                agent_id=agent_id,
                status=current_state,
                timestamp=now,
                current_task_id=current_task_id,
                active_model=active_model
                or getattr(self._heartbeats.get(agent_id), "active_model", None)
                or self._manifests[agent_id].model_preference
                or "auto",
                runtime_health="healthy" if not error else "degraded",
                last_error=error,
                available_tools=self._resolve_tools(self._manifests[agent_id]),
                current_workload=1 if current_task_id else 0,
            )
            self._heartbeats[agent_id] = hb
            self.store.update_runtime_status(
                agent_id=agent_id,
                status=current_state,
                current_task_id=current_task_id,
                active_model=hb.active_model,
                runtime_health=hb.runtime_health,
                last_error=error,
            )

    def set_agent_state(
        self,
        agent_id: str,
        state: AgentRuntimeState,
        current_task_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._states[agent_id] = state
            if current_task_id:
                self._active_tasks[agent_id] = current_task_id
            elif state in (
                AgentRuntimeState.IDLE,
                AgentRuntimeState.OFFLINE,
                AgentRuntimeState.FAILED,
            ):
                self._active_tasks.pop(agent_id, None)

            self.store.update_runtime_status(
                agent_id=agent_id,
                status=state,
                current_task_id=self._active_tasks.get(agent_id),
                last_error=error,
            )

    # --- Query & Summary Methods ---

    def get_agent_manifest(self, agent_id: str) -> Optional[AgentManifest]:
        return self._manifests.get(agent_id)

    def get_agent_state(self, agent_id: str) -> AgentRuntimeState:
        return self._states.get(agent_id, AgentRuntimeState.OFFLINE)

    def get_agent_heartbeat(self, agent_id: str) -> Optional[AgentHeartbeat]:
        return self._heartbeats.get(agent_id)

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return list of all agents with manifest and real live runtime state."""
        with self._lock:
            results = []
            for m in self._manifests.values():
                st = self._states.get(m.id, AgentRuntimeState.OFFLINE)
                hb = self._heartbeats.get(m.id)
                results.append(
                    {
                        "id": m.id,
                        "display_name": m.display_name,
                        "role": m.role,
                        "description": m.description,
                        "avatar": m.avatar,
                        "enabled": m.enabled,
                        "status": st.value,
                        "active_model": hb.active_model
                        if (hb and hb.active_model)
                        else (m.model_preference or "auto"),
                        "current_task_id": self._active_tasks.get(m.id),
                        "runtime_health": hb.runtime_health if hb else "offline",
                        "last_heartbeat": hb.timestamp if hb else None,
                        "last_error": hb.last_error if hb else None,
                        "allowed_tools": m.allowed_tools,
                        "denied_tools": m.denied_tools,
                        "version": m.version,
                    }
                )
            return results

    def get_team_summary(self) -> TeamSummary:
        """Compute aggregated live team numbers."""
        with self._lock:
            total = len(self._manifests)
            online = sum(
                1
                for s in self._states.values()
                if s
                in (
                    AgentRuntimeState.IDLE,
                    AgentRuntimeState.BUSY,
                    AgentRuntimeState.ONLINE,
                    AgentRuntimeState.WAITING_APPROVAL,
                    AgentRuntimeState.PAUSED,
                    AgentRuntimeState.DEGRADED,
                )
            )
            idle = sum(
                1 for s in self._states.values() if s == AgentRuntimeState.IDLE
            )
            busy = sum(
                1 for s in self._states.values() if s == AgentRuntimeState.BUSY
            )
            waiting = sum(
                1
                for s in self._states.values()
                if s == AgentRuntimeState.WAITING_APPROVAL
            )
            degraded = sum(
                1
                for s in self._states.values()
                if s == AgentRuntimeState.DEGRADED
            )
            offline = sum(
                1
                for s in self._states.values()
                if s == AgentRuntimeState.OFFLINE
            )
            failed = sum(
                1 for s in self._states.values() if s == AgentRuntimeState.FAILED
            )
            return TeamSummary(
                total_registered=total,
                total_online=online,
                total_idle=idle,
                total_busy=busy,
                total_waiting_approval=waiting,
                total_degraded=degraded,
                total_offline=offline,
                total_failed=failed,
                active_tasks=len(self._active_tasks),
            )

    def count_online(self) -> int:
        summary = self.get_team_summary()
        return summary.total_online

    # --- Natural Language Status Engine ---

    def answer_status_query(self, query: str) -> Optional[str]:
        """Inspect query text and generate natural language answers from live registry data.
        Returns response string if query matches an agent/team status question, else None.
        """
        q = query.strip().lower()

        # 1. Team count query
        if re.search(
            r"\b(how many agents|agent count|agents online|team status|show team)\b",
            q,
        ):
            summary = self.get_team_summary()
            return (
                f"Sir, {summary.total_registered} agents are registered. "
                f"{summary.total_online} are currently online ({summary.total_idle} idle, {summary.total_busy} busy with tasks). "
                f"{summary.total_offline} offline."
            )

        # 2. Available agents query
        if re.search(r"\b(which agents are (available|online|free|idle))\b", q):
            idle_agents = [
                m.display_name
                for aid, m in self._manifests.items()
                if self._states.get(aid) == AgentRuntimeState.IDLE
            ]
            if idle_agents:
                return (
                    f"The following agents are currently idle and ready for tasks: "
                    f"{', '.join(idle_agents)}."
                )
            return (
                "All online agents are currently occupied with active tasks, sir."
            )

        # 3. Busy agents query
        if re.search(
            r"\b(which agents are busy|who is working|active agents)\b", q
        ):
            busy = [
                f"{m.display_name} ({m.role})"
                for aid, m in self._manifests.items()
                if self._states.get(aid) == AgentRuntimeState.BUSY
            ]
            if busy:
                return f"Currently active agents: {'; '.join(busy)}."
            return (
                "No agents are currently busy. All specialists are standing by."
            )

        # 4. Individual agent query: "What is Forge doing?" / "Status of Oracle"
        for aid, m in self._manifests.items():
            pattern = rf"\b(what is {aid} doing|status of {aid}|where is {aid}|is {aid} (online|available))\b"
            if re.search(pattern, q):
                st = self._states.get(aid, AgentRuntimeState.OFFLINE)
                task_id = self._active_tasks.get(aid)
                hb = self._heartbeats.get(aid)
                if st == AgentRuntimeState.BUSY and task_id:
                    return f"{m.display_name} ({m.role}) is currently BUSY executing task {task_id}."
                elif st == AgentRuntimeState.IDLE:
                    return f"{m.display_name} ({m.role}) is ONLINE and currently IDLE, ready for assignments."
                elif st == AgentRuntimeState.DEGRADED:
                    return f"{m.display_name} ({m.role}) is in a DEGRADED state ({hb.last_error if hb else 'unknown reason'})."
                else:
                    return f"{m.display_name} is currently {st.value.upper()}."

        return None

    def generate_startup_announcement(self) -> str:
        """Produce the natural-language team startup announcement."""
        summary = self.get_team_summary()
        lines = [
            "Good day, sir. Jarvis is online.",
            "The multi-agent workforce has completed initialization.",
            "",
        ]
        for aid, m in self._manifests.items():
            st = self._states.get(aid, AgentRuntimeState.OFFLINE).value.title()
            lines.append(f"{m.display_name} — {m.role}: {st}.")

        lines.append("")
        lines.append(
            f"{summary.total_online} of {summary.total_registered} agents are online and operational. "
            "Awaiting your instructions, sir."
        )
        return "\n".join(lines)


def get_workforce_registry(
    store: Optional[WorkforceStore] = None,
    bus: Optional[EventBus] = None,
) -> AgentWorkforceRegistry:
    """Return singleton workforce registry."""
    global _GLOBAL_WORKFORCE_REGISTRY
    if _GLOBAL_WORKFORCE_REGISTRY is None:
        _GLOBAL_WORKFORCE_REGISTRY = AgentWorkforceRegistry(
            store=store, bus=bus
        )
    return _GLOBAL_WORKFORCE_REGISTRY
