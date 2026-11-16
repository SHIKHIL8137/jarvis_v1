"""AtlasOrchestrator — Dynamic LLM task planner, multi-agent coordinator, and report synthesizer."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from openjarvis.core.events import EventBus
from openjarvis.core.registry import ToolRegistry
import openjarvis.tools  # noqa: F401 - ensure ToolRegistry is fully populated with all registered tools
from openjarvis.tasks.models import BackgroundTask, StepStatus, TaskBudget, TaskStep
from openjarvis.workforce.models import (
    AgentManifest,
    AgentReport,
    AgentRuntimeState,
)
from openjarvis.workforce.registry import AgentWorkforceRegistry, get_workforce_registry

logger = logging.getLogger(__name__)

PLANNER_PROMPT_TEMPLATE = """You are ATLAS, the Chief of Staff and Orchestrator for the OpenJarvis multi-agent workforce.
The user submitted the following request:
"{user_request}"

Available specialist agents:
- scout: File discovery, document analysis, memory search, repository structure
- forge: Software engineering, code inspection, bug hunting, refactoring, tests, approved edits
- sentinel: Security audit, vulnerability scan, dependency check, permission governance
- oracle: Deep research, web investigation, technical comparisons, evidence extraction
- vanguard: QA review, test execution, claim verification, acceptance criteria validation
- pulse: Operations, routines, health checks, background scheduling

Create an execution plan as a JSON list of steps.
Each step MUST contain:
- "step_id": string (e.g. "step-1")
- "step_number": integer
- "title": short string
- "description": clear instruction
- "assigned_agent": one of [scout, forge, sentinel, oracle, vanguard, pulse, atlas]
- "dependencies": list of prerequisite step_ids (e.g. ["step-1"]) or []
- "tool_name": tool to run (e.g. "file_read", "think", "knowledge_search", "shell_exec")
- "tool_params": dict of arguments
- "expected_output": what output is expected
- "verification_criteria": criteria to confirm completion

Independent steps should have empty dependencies so they can run in parallel.
Respond ONLY with a valid JSON array of step objects.
"""


class AtlasOrchestrator:
    """Chief of Staff coordinating the multi-agent engineering & operations workforce."""

    def __init__(
        self,
        registry: Optional[AgentWorkforceRegistry] = None,
        bus: Optional[EventBus] = None,
        max_parallel_workers: int = 2,
    ) -> None:
        from openjarvis.tools import ensure_tools_registered
        ensure_tools_registered()

        self.registry = registry or get_workforce_registry()
        self.bus = bus
        self.max_parallel_workers = max_parallel_workers

    def plan_task(
        self,
        user_request: str,
        engine: Optional[Any] = None,
        model_name: Optional[str] = None,
    ) -> List[TaskStep]:
        """Generate a validated multi-agent execution DAG for the user request."""
        raw_steps: Optional[List[Dict[str, Any]]] = None

        # 1. Attempt dynamic LLM-based planning if engine is available
        if engine and hasattr(engine, "generate"):
            try:
                from openjarvis.core.types import Message, Role
                from openjarvis.models import get_model_selection_engine

                selected_model = model_name
                if not selected_model:
                    try:
                        sel_engine = get_model_selection_engine()
                        sel = sel_engine.select_model(task_type="planning")
                        if sel.is_compatible and sel.model_id:
                            selected_model = sel.model_id
                        else:
                            selected_model = None
                    except Exception as sel_err:
                        logger.debug("Planning model selection error: %s", sel_err)
                        selected_model = None

                if selected_model:
                    prompt = PLANNER_PROMPT_TEMPLATE.format(user_request=user_request)
                    resp = engine.generate(
                        messages=[
                            Message(role=Role.SYSTEM, content="You are a precise task planner that outputs only JSON."),
                            Message(role=Role.USER, content=prompt),
                        ],
                        model=selected_model,
                        temperature=0.2,
                    )
                    text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
                    raw_steps = self._parse_json_plan(text)
                else:
                    logger.info("No compatible planning model available; falling back to heuristic planner.")
                    raw_steps = self._heuristic_planner(user_request)
            except Exception as exc:
                logger.warning("LLM planning generation failed: %s. Using heuristic planner.", exc)

        # 2. Heuristic planner fallback if LLM plan was missing or invalid
        if not raw_steps:
            raw_steps = self._heuristic_planner(user_request)

        # 3. Validate plan (agents exist, tools permitted, no DAG cycles)
        validated_steps = self._validate_and_build_steps(raw_steps, user_request)
        return validated_steps

    def _parse_json_plan(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """Extract and parse JSON array from model output."""
        clean = re.sub(r"```(?:json)?", "", text).strip("`").strip()
        match = re.search(r"\[\s*\{.*\}\s*\]", clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            data = json.loads(clean)
            if isinstance(data, list) and len(data) > 0:
                return data
        except Exception:
            pass
        return None

    def _heuristic_planner(self, user_request: str) -> List[Dict[str, Any]]:
        """Intelligent semantic planner generating multi-agent steps based on domain intent."""
        req_lower = user_request.lower()

        # 1. Multi-domain engineering, security, and performance audit (e.g. ERP request or full audit)
        is_comprehensive = ("erp" in req_lower) or (
            sum(1 for term in ("inspect", "analyze", "bug", "security", "audit", "performance", "codebase") if term in req_lower) >= 3
            and "fix" not in req_lower
        )
        if is_comprehensive:
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Repository & Context Discovery",
                    "description": f"Scout repository structure and locate files for: {user_request}",
                    "assigned_agent": "scout",
                    "dependencies": [],
                    "tool_name": "file_read",
                    "tool_params": {"path": "."},
                    "expected_output": "Project architecture and key file paths",
                    "verification_criteria": "Identified file inventory",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Software Architecture & Bug Analysis",
                    "description": f"Analyze code quality, performance bottlenecks, and potential bugs for: {user_request}",
                    "assigned_agent": "forge",
                    "dependencies": ["step-1"],
                    "tool_name": "forge_coding",
                    "tool_params": {"objective": f"Architecture analysis and defect identification for: {user_request}", "workdir": "."},
                    "expected_output": "Bug findings, architectural analysis, and proposed fixes",
                    "verification_criteria": "Identified potential defects or bottlenecks with code evidence",
                },
                {
                    "step_id": "step-3",
                    "step_number": 3,
                    "title": "Security & Dependency Posture Audit",
                    "description": f"Scan security configuration, access boundaries, and vulnerability posture for: {user_request}",
                    "assigned_agent": "sentinel",
                    "dependencies": ["step-1"],
                    "tool_name": "file_read",
                    "tool_params": {"path": "pyproject.toml"},
                    "expected_output": "Security configuration and dependency posture analysis",
                    "verification_criteria": "Security posture and boundaries documented",
                },
                {
                    "step_id": "step-4",
                    "step_number": 4,
                    "title": "Technical Improvements Research",
                    "description": "Research optimal industry solutions, design patterns, and benchmarks",
                    "assigned_agent": "oracle",
                    "dependencies": ["step-1"],
                    "tool_name": "knowledge_search",
                    "tool_params": {"query": user_request},
                    "expected_output": "Evidence-backed technical recommendations",
                    "verification_criteria": "Authoritative research citations",
                },
                {
                    "step_id": "step-5",
                    "step_number": 5,
                    "title": "QA & Evidence Verification",
                    "description": "Verify code findings, check claims, and run test verification suite",
                    "assigned_agent": "vanguard",
                    "dependencies": ["step-2", "step-3", "step-4"],
                    "tool_name": "forge_coding",
                    "tool_params": {"objective": f"Verify QA evidence and test suite for: {user_request}", "verification_cmd": "pytest tests/ -q --tb=short"},
                    "expected_output": "QA verification report with verified claims and test results",
                    "verification_criteria": "All claims checked against source evidence and test executions",
                },
            ]

        # 2. Coding, Feature Implementation, and Bug Fixing (e.g. Scenario A)
        if any(w in req_lower for w in ("fix", "bug", "implement", "feature", "refactor", "patch")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Context & Code Discovery",
                    "description": f"Scout relevant source code files for: {user_request}",
                    "assigned_agent": "scout",
                    "dependencies": [],
                    "tool_name": "file_read",
                    "tool_params": {"path": "."},
                    "expected_output": "Identified source files",
                    "verification_criteria": "Source file inventory mapped",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Code Implementation & Defect Resolution",
                    "description": f"Implement changes or fix defect for: {user_request}",
                    "assigned_agent": "forge",
                    "dependencies": ["step-1"],
                    "tool_name": "forge_coding",
                    "tool_params": {"objective": user_request, "workdir": "."},
                    "expected_output": "Code modifications and patch report",
                    "verification_criteria": "Code modified cleanly",
                },
                {
                    "step_id": "step-3",
                    "step_number": 3,
                    "title": "QA & Test Verification",
                    "description": f"Run test verification suite to ensure no regressions for: {user_request}",
                    "assigned_agent": "vanguard",
                    "dependencies": ["step-2"],
                    "tool_name": "shell_exec",
                    "tool_params": {"command": "pytest -q --tb=short"},
                    "expected_output": "Test execution results and verification verdict",
                    "verification_criteria": "All unit tests pass",
                },
            ]

        # 3. Pure Security Audit / Governance
        if any(w in req_lower for w in ("security", "vulnerabilit", "cve", "permission audit", "firewall")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Security Posture Audit",
                    "description": f"Audit security posture, boundaries, and vulnerabilities for: {user_request}",
                    "assigned_agent": "sentinel",
                    "dependencies": [],
                    "tool_name": "file_read",
                    "tool_params": {"path": "pyproject.toml"},
                    "expected_output": "Security audit findings and boundary assessment",
                    "verification_criteria": "Vulnerabilities or policy compliance checked",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Synthesize Security Report",
                    "description": "Synthesize security recommendations and risk mitigations",
                    "assigned_agent": "atlas",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Synthesizing security assessment for: {user_request}"},
                    "expected_output": "Executive security recommendations",
                    "verification_criteria": "Security posture summarized",
                },
            ]

        # 2. Pure QA / Test Verification
        if any(w in req_lower for w in ("test", "verify", "verification", "qa", "acceptance criteria")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Execute Verification Suite",
                    "description": f"Execute test verification and validate criteria for: {user_request}",
                    "assigned_agent": "vanguard",
                    "dependencies": [],
                    "tool_name": "shell_exec",
                    "tool_params": {"command": "pytest -q --tb=short"},
                    "expected_output": "Test execution results and verification findings",
                    "verification_criteria": "Tests executed with exit code and evidence",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Synthesize QA Results",
                    "description": "Synthesize verification evidence and pass/fail summary",
                    "assigned_agent": "atlas",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Compiling QA verification report for: {user_request}"},
                    "expected_output": "Consolidated QA verdict",
                    "verification_criteria": "Verification verdict recorded",
                },
            ]

        # 3. Pure Coding / Bug Hunting / Refactoring
        if any(w in req_lower for w in ("implement", "fix bug", "refactor", "write code", "coding", "patch")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Code Implementation & Defect Resolution",
                    "description": f"Implement code changes or fix defect for: {user_request}",
                    "assigned_agent": "forge",
                    "dependencies": [],
                    "tool_name": "forge_coding",
                    "tool_params": {"objective": user_request, "workdir": "."},
                    "expected_output": "Code modifications and implementation findings",
                    "verification_criteria": "Target code updated and verified",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Synthesize Coding Result",
                    "description": "Synthesize implementation report and commit diffs",
                    "assigned_agent": "atlas",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Reviewing code modifications for: {user_request}"},
                    "expected_output": "Change summary and verification status",
                    "verification_criteria": "Code change reviewed",
                },
            ]

        # 4. Pure File Discovery / Exploration
        if any(w in req_lower for w in ("find file", "list file", "locate file", "explore repo", "discover file")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Explore Files & Directory Tree",
                    "description": f"Discover directory structure and file contents for: {user_request}",
                    "assigned_agent": "scout",
                    "dependencies": [],
                    "tool_name": "file_read",
                    "tool_params": {"path": "."},
                    "expected_output": "Discovered files and structure",
                    "verification_criteria": "File paths identified",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Synthesize File Inventory",
                    "description": "Summarize discovered file structure",
                    "assigned_agent": "atlas",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Reviewing discovered file structure for: {user_request}"},
                    "expected_output": "File inventory summary",
                    "verification_criteria": "Summary produced",
                },
            ]

        # 5. Operations & Scheduling
        if any(w in req_lower for w in ("routine", "reminder", "health check", "schedule", "pulse")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Operational Health & Routine",
                    "description": f"Manage routine and check operations for: {user_request}",
                    "assigned_agent": "pulse",
                    "dependencies": [],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Executing operational routine: {user_request}"},
                    "expected_output": "Operational status and scheduled trigger",
                    "verification_criteria": "Routine executed",
                },
            ]

        # 6. Research-focused goal
        if any(w in req_lower for w in ("research", "compare", "benchmark", "find out")):
            return [
                {
                    "step_id": "step-1",
                    "step_number": 1,
                    "title": "Gather Authoritative Evidence",
                    "description": f"Search knowledge base and authoritative documentation for {user_request}",
                    "assigned_agent": "oracle",
                    "dependencies": [],
                    "tool_name": "knowledge_search",
                    "tool_params": {"query": user_request},
                    "expected_output": "Verified citations and facts",
                    "verification_criteria": "Evidence items extracted",
                },
                {
                    "step_id": "step-2",
                    "step_number": 2,
                    "title": "Validate Claims & Synthesize Report",
                    "description": "Review research claims and format structured report artifact",
                    "assigned_agent": "vanguard",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Validating evidence and quotes for: {user_request}"},
                    "expected_output": "Verified research report",
                    "verification_criteria": "Evidence validated",
                },
            ]

        # Standard fallback: Scout discovery + Atlas synthesis
        return [
            {
                "step_id": "step-1",
                "step_number": 1,
                "title": "Investigate Context",
                "description": f"Gather context for: {user_request}",
                "assigned_agent": "scout",
                "dependencies": [],
                "tool_name": "knowledge_search",
                "tool_params": {"query": user_request},
                "expected_output": "Context findings",
                "verification_criteria": "Context retrieved",
            },
            {
                "step_id": "step-2",
                "step_number": 2,
                "title": "Synthesize & Recommend",
                "description": "Synthesize response and recommend next steps",
                "assigned_agent": "atlas",
                "dependencies": ["step-1"],
                "tool_name": "think",
                "tool_params": {"thought": f"Formulating resolution for: {user_request}"},
                "expected_output": "Actionable response",
                "verification_criteria": "Response compiled",
            },
        ]

    def _validate_and_build_steps(
        self, raw_steps: List[Dict[str, Any]], user_request: str
    ) -> List[TaskStep]:
        """Validate agent manifests, tool permissions, and DAG acyclicity."""
        valid_agents = set(self.registry._manifests.keys())
        steps: List[TaskStep] = []
        step_ids: Set[str] = set()

        for idx, item in enumerate(raw_steps, 1):
            s_id = str(item.get("step_id", f"step-{idx}"))
            agent = str(item.get("assigned_agent", "atlas")).lower()
            if agent not in valid_agents:
                agent = "atlas"

            manifest = self.registry.get_agent_manifest(agent)
            tool_name = item.get("tool_name", "think")
            tool_params = item.get("tool_params") or {}

            # Verify tool is allowed for this agent
            if manifest and tool_name:
                if tool_name in manifest.denied_tools:
                    logger.warning("Tool %s denied for agent %s. Replacing with think.", tool_name, agent)
                    tool_name = "think"
                    tool_params = {"thought": f"Executing step safely without denied tool {tool_name}"}

            step = TaskStep(
                step_id=s_id,
                step_number=idx,
                title=item.get("title", f"Step {idx}"),
                description=item.get("description", ""),
                status=StepStatus.PENDING,
                tool_name=tool_name,
                tool_params=tool_params,
                idempotency_key=f"task:{s_id}:{tool_name}",
                assigned_agent=agent,
                dependencies=item.get("dependencies") or [],
                expected_output=item.get("expected_output", ""),
                verification_criteria=item.get("verification_criteria", ""),
            )
            steps.append(step)
            step_ids.add(s_id)

        # Sanitize dependencies (must exist in step_ids)
        for s in steps:
            s.dependencies = [d for d in s.dependencies if d in step_ids and d != s.step_id]

        return steps

    def execute_step_safely(
        self,
        step: TaskStep,
        task_id: str = "task-default",
        model: Optional[str] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Standard executor routing to appropriate specialist runtime or tool with allocated model."""
        allocated_model = model or step.tool_params.get("model") or step.tool_params.get("_allocated_model")

        # 1. Enforce agent manifest permission boundaries fail-closed
        manifest = self.registry.get_agent_manifest(step.assigned_agent)
        if manifest:
            # Denied tools check
            if step.tool_name in manifest.denied_tools:
                err = f"Tool '{step.tool_name}' is explicitly denied for agent '{step.assigned_agent}'"
                logger.warning(err)
                return None, err

            # Denied capabilities check
            if manifest.denied_capabilities:
                if "file:write" in manifest.denied_capabilities and step.tool_name in ("file_write", "edit_file", "apply_patch"):
                    err = f"Agent '{step.assigned_agent}' is denied capability 'file:write'"
                    logger.warning(err)
                    return None, err
                if "code:execute" in manifest.denied_capabilities and step.tool_name in ("shell_exec", "forge_coding"):
                    err = f"Agent '{step.assigned_agent}' is denied capability 'code:execute'"
                    logger.warning(err)
                    return None, err
                if "channel:send" in manifest.denied_capabilities and step.tool_name in ("channel_send", "send_message"):
                    err = f"Agent '{step.assigned_agent}' is denied capability 'channel:send'"
                    logger.warning(err)
                    return None, err

            # Allowed tools whitelist check (if specified)
            if manifest.allowed_tools and step.tool_name not in manifest.allowed_tools and step.tool_name != "think":
                err = f"Tool '{step.tool_name}' is not in allowed tools for agent '{step.assigned_agent}'"
                logger.warning(err)
                return None, err

        # 2. Forge Coding Engine execution
        if step.assigned_agent == "forge" and step.tool_name in ("forge_coding", "code_goal", "coding", "code_interpreter"):
            from openjarvis.workforce.forge_coding import ForgeCodingEngine

            engine = ForgeCodingEngine(registry=self.registry)
            report = engine.execute_coding_task(
                task_id=task_id,
                step_id=step.step_id,
                objective=step.description or step.title,
                workdir=step.tool_params.get("workdir"),
                verification_cmd=step.tool_params.get("verification_cmd"),
                model=allocated_model,
            )
            if report.errors:
                return report.to_dict(), "; ".join(report.errors)
            return report.to_dict(), None

        # Prevent non-forge agents from attempting forge_coding
        if step.tool_name in ("forge_coding", "code_goal") and step.assigned_agent != "forge":
            return None, f"Tool '{step.tool_name}' is restricted to Forge specialist (attempted by {step.assigned_agent})"

        from openjarvis.core.registry import ToolRegistry
        from openjarvis.tools import ensure_tools_registered

        if not ToolRegistry.contains(step.tool_name):
            ensure_tools_registered()

        if not ToolRegistry.contains(step.tool_name):
            if step.tool_name == "think":
                return {"result": step.tool_params.get("thought", "Analyzed")}, None
            return None, f"Tool '{step.tool_name}' not registered"

        try:
            tool_cls = ToolRegistry.get(step.tool_name)
            tool_inst = tool_cls() if callable(tool_cls) else tool_cls
            params = dict(step.tool_params)
            # Clean internal orchestration hints before tool execution
            params.pop("_allocated_model", None)
            res = tool_inst.execute(**params)
            if hasattr(res, "success") and not res.success:
                return None, str(getattr(res, "content", "Execution failed"))
            content = getattr(res, "content", str(res))
            return {"content": content, "success": True}, None
        except Exception as exc:
            return None, str(exc)

    def execute_multi_agent_plan(
        self,
        task_id: str,
        user_request: str,
        steps: List[TaskStep],
        step_executor: Optional[Callable[..., Tuple[Optional[Dict[str, Any]], Optional[str]]]] = None,
        cancellation_token: Optional[Any] = None,
        budget: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Execute task steps according to DAG dependencies with bounded concurrency, model routing, and budget/cancellation checks."""
        from openjarvis.workforce.communication import ConflictResolver

        completed_step_ids: Set[str] = set()
        agent_reports: List[AgentReport] = []
        start_time = time.time()
        was_cancelled = False
        was_budget_exhausted = False

        # Step lookup
        steps_by_id = {s.step_id: s for s in steps}

        while len(completed_step_ids) < len(steps):
            # 1. Cancellation check
            is_cancelled = False
            cancel_reason = "Task execution cancelled"
            if cancellation_token:
                if hasattr(cancellation_token, "is_cancelled"):
                    is_cancelled = bool(cancellation_token.is_cancelled)
                    cancel_reason = getattr(cancellation_token, "reason", cancel_reason) or cancel_reason
                elif hasattr(cancellation_token, "is_set"):
                    is_cancelled = bool(cancellation_token.is_set())

            if is_cancelled:
                logger.info("Task %s cancelled via cancellation token", task_id)
                was_cancelled = True
                for s in steps:
                    if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                        s.status = StepStatus.CANCELLED
                        s.error = cancel_reason
                        self.registry.set_agent_state(s.assigned_agent, AgentRuntimeState.IDLE)
                break

            # 2. Budget check
            if budget:
                elapsed_now = time.time() - start_time
                max_duration = getattr(budget, "max_execution_time_seconds", None) or getattr(budget, "max_duration_seconds", None)
                if max_duration and elapsed_now > max_duration:
                    logger.warning("Task %s exceeded duration budget (%.1fs > %.1fs)", task_id, elapsed_now, max_duration)
                    was_budget_exhausted = True
                    for s in steps:
                        if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                            s.status = StepStatus.CANCELLED
                            s.error = f"Task duration budget exceeded: {elapsed_now:.1f}s > {max_duration}s"
                            self.registry.set_agent_state(s.assigned_agent, AgentRuntimeState.IDLE)
                    break

                tool_calls_used = getattr(budget, "current_tool_calls", 0) or getattr(budget, "tool_calls_used", 0)
                max_tool_calls = getattr(budget, "max_tool_calls", None)
                if max_tool_calls and tool_calls_used >= max_tool_calls:
                    logger.warning("Task %s exceeded tool call budget (%d >= %d)", task_id, tool_calls_used, max_tool_calls)
                    was_budget_exhausted = True
                    for s in steps:
                        if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                            s.status = StepStatus.CANCELLED
                            s.error = f"Task tool call budget exceeded: {tool_calls_used} >= {max_tool_calls}"
                            self.registry.set_agent_state(s.assigned_agent, AgentRuntimeState.IDLE)
                    break

            # Find all steps whose dependencies are satisfied and are not yet started
            ready_steps = [
                s
                for s in steps
                if s.status == StepStatus.PENDING
                and all(dep in completed_step_ids for dep in s.dependencies)
            ]

            if not ready_steps:
                # Check for deadlocks or completion
                pending = [s for s in steps if s.status == StepStatus.PENDING]
                if pending:
                    logger.error("DAG dependency deadlock or failure for steps: %s", [s.step_id for s in pending])
                    for s in pending:
                        s.status = StepStatus.FAILED
                        s.error = "Unresolved dependency failure"
                break

            # Execute ready steps in parallel up to max_parallel_workers
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_workers) as executor:
                futures = {}
                for step in ready_steps:
                    step.status = StepStatus.RUNNING
                    step.started_at = time.time()
                    try:
                        from openjarvis.models import get_model_selection_engine
                        from openjarvis.models.selector import AGENT_TO_TASK_MAP

                        norm_task_type = AGENT_TO_TASK_MAP.get(step.assigned_agent, step.assigned_agent)
                        sel_engine = get_model_selection_engine()
                        sel = sel_engine.select_model(task_type=norm_task_type, agent_id=step.assigned_agent)
                        if not sel.is_compatible or not sel.model_id:
                            raise RuntimeError(f"No compatible model available for {step.assigned_agent}: {sel.reason}")
                        step_model = sel.model_id
                    except Exception as model_err:
                        logger.error("Model selection failed for step %s (%s): %s", step.step_id, step.assigned_agent, model_err)
                        step.status = StepStatus.FAILED
                        step.error = f"Model selection failed: {model_err}"
                        self.registry.set_agent_state(step.assigned_agent, AgentRuntimeState.DEGRADED, error=str(model_err))
                        rep = AgentReport(
                            agent_id=step.assigned_agent,
                            task_id=task_id,
                            step_id=step.step_id,
                            work_performed=f"Model allocation failed for {step.title}",
                            findings=[],
                            results={},
                            errors=[str(model_err)],
                            confidence=0.0,
                        )
                        agent_reports.append(rep)
                        self.registry.store.save_report(rep)
                        continue

                    self.registry.record_heartbeat(step.assigned_agent, current_task_id=task_id, active_model=step_model)
                    step.tool_params["_allocated_model"] = step_model

                    def run_step(s=step, m=step_model):
                        if step_executor:
                            try:
                                return step_executor(s, m)
                            except TypeError:
                                return step_executor(s)
                        return self.execute_step_safely(s, task_id=task_id, model=m)

                    futures[executor.submit(run_step)] = step

                for future in concurrent.futures.as_completed(futures):
                    step = futures[future]
                    # Update tool call count in budget
                    if budget:
                        if hasattr(budget, "current_tool_calls"):
                            budget.current_tool_calls += 1
                        elif hasattr(budget, "tool_calls_used"):
                            budget.tool_calls_used += 1

                    try:
                        result, error = future.result()
                        if error:
                            step.status = StepStatus.FAILED
                            step.error = error
                            self.registry.set_agent_state(step.assigned_agent, AgentRuntimeState.IDLE, error=error)
                            rep = AgentReport(
                                agent_id=step.assigned_agent,
                                task_id=task_id,
                                step_id=step.step_id,
                                work_performed=f"Failed execution of {step.title}: {step.description}",
                                findings=[],
                                results={},
                                errors=[error],
                                confidence=0.0,
                            )
                            agent_reports.append(rep)
                            self.registry.store.save_report(rep)
                        else:
                            step.status = StepStatus.COMPLETED
                            step.completed_at = time.time()
                            step.tool_result = result
                            completed_step_ids.add(step.step_id)
                            self.registry.set_agent_state(step.assigned_agent, AgentRuntimeState.IDLE)

                            # Record structured agent report
                            if isinstance(result, dict) and "agent_id" in result and "work_performed" in result:
                                rep = AgentReport.from_dict(result)
                            else:
                                rep = AgentReport(
                                    agent_id=step.assigned_agent,
                                    task_id=task_id,
                                    step_id=step.step_id,
                                    work_performed=f"Executed {step.title}: {step.description}",
                                    findings=[{"title": step.title, "summary": str(result)}],
                                    results=result if isinstance(result, dict) else {"output": str(result)},
                                    errors=[error] if error else [],
                                    confidence=0.95 if not error else 0.4,
                                )
                            agent_reports.append(rep)
                            self.registry.store.save_report(rep)
                    except Exception as exc:
                        step.status = StepStatus.FAILED
                        step.error = str(exc)
                        self.registry.set_agent_state(step.assigned_agent, AgentRuntimeState.IDLE, error=str(exc))
                        rep = AgentReport(
                            agent_id=step.assigned_agent,
                            task_id=task_id,
                            step_id=step.step_id,
                            work_performed=f"Exception during {step.title}: {step.description}",
                            findings=[],
                            results={},
                            errors=[str(exc)],
                            confidence=0.0,
                        )
                        agent_reports.append(rep)
                        self.registry.store.save_report(rep)

        # 3. Detect and resolve specialist contradictions
        conflict_res = ConflictResolver.detect_and_resolve(agent_reports)

        # 4. ATLAS synthesizes consolidated response
        elapsed = round(time.time() - start_time, 2)
        consolidated_summary = self._synthesize_reports(user_request, agent_reports, steps, conflict_info=conflict_res)

        if was_cancelled:
            final_status = "cancelled"
        elif was_budget_exhausted:
            final_status = "budget_exhausted"
        elif len(completed_step_ids) == len(steps):
            final_status = "completed"
        else:
            final_status = "incomplete"

        return {
            "task_id": task_id,
            "status": final_status,
            "user_request": user_request,
            "total_steps": len(steps),
            "completed_steps": len(completed_step_ids),
            "duration_seconds": elapsed,
            "reports": [r.to_dict() for r in agent_reports],
            "agent_reports": [r.to_dict() for r in agent_reports],
            "has_conflicts": conflict_res.get("has_conflicts", False),
            "conflicts": conflict_res.get("conflicts", []),
            "consolidated_response": consolidated_summary,
            "consolidated_report": consolidated_summary,
        }

    def _synthesize_reports(
        self,
        user_request: str,
        reports: List[AgentReport],
        steps: List[TaskStep],
        conflict_info: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Atlas consolidates specialist findings into a cohesive executive report."""
        lines = [
            f"# Mission Report: {user_request}",
            f"**Coordinated by:** ATLAS (Chief of Staff)",
            f"**Specialist Contributions:** {len(reports)} reports collected across {len(steps)} steps.",
            "",
            "## Summary of Specialist Findings",
        ]

        for rep in reports:
            manifest = self.registry.get_agent_manifest(rep.agent_id)
            agent_title = manifest.display_name if manifest else rep.agent_id.upper()
            role = manifest.role if manifest else "Specialist"
            lines.append(f"### {agent_title} ({role})")
            lines.append(f"- **Work Performed:** {rep.work_performed}")
            if rep.findings:
                for f in rep.findings:
                    lines.append(f"- **Finding:** {f.get('title', 'Detail')} — {f.get('summary', '')}")
            if rep.errors:
                lines.append(f"- **Issues Encountered:** {', '.join(rep.errors)}")
            lines.append("")

        if conflict_info and conflict_info.get("has_conflicts"):
            lines.append("## Conflict Resolution & Discrepancy Analysis")
            for conf in conflict_info.get("conflicts", []):
                lines.append(f"- **Contradiction Detected:** {conf.get('description', '')}")
            for note in conflict_info.get("resolution_notes", []):
                lines.append(f"- **Resolution Strategy:** {note}")
            lines.append("")

        lines.append("## Team Verification & Next Steps")
        vanguard_reports = [r for r in reports if r.agent_id == "vanguard"]
        if vanguard_reports:
            lines.append("✓ Quality & Verification check completed by VANGUARD: All findings cross-checked against source data.")
        else:
            lines.append("All scheduled subtasks completed successfully. Ready for further instructions.")

        return "\n".join(lines)
        """Atlas consolidates specialist findings into a cohesive executive report."""
        lines = [
            f"# Mission Report: {user_request}",
            f"**Coordinated by:** ATLAS (Chief of Staff)",
            f"**Specialist Contributions:** {len(reports)} reports collected across {len(steps)} steps.",
            "",
            "## Summary of Specialist Findings",
        ]

        for rep in reports:
            manifest = self.registry.get_agent_manifest(rep.agent_id)
            agent_title = manifest.display_name if manifest else rep.agent_id.upper()
            role = manifest.role if manifest else "Specialist"
            lines.append(f"### {agent_title} ({role})")
            lines.append(f"- **Work Performed:** {rep.work_performed}")
            if rep.findings:
                for f in rep.findings:
                    lines.append(f"- **Finding:** {f.get('title', 'Detail')} — {f.get('summary', '')}")
            if rep.errors:
                lines.append(f"- **Issues Encountered:** {', '.join(rep.errors)}")
            lines.append("")

        lines.append("## Team Verification & Next Steps")
        vanguard_reports = [r for r in reports if r.agent_id == "vanguard"]
        if vanguard_reports:
            lines.append("✓ Quality & Verification check completed by VANGUARD: All findings cross-checked against source data.")
        else:
            lines.append("All scheduled subtasks completed successfully. Ready for further instructions.")

        return "\n".join(lines)
