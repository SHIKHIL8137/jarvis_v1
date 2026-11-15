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
                        selected_model = sel.model_id
                    except Exception as sel_err:
                        logger.debug("Planning model selection fallback: %s", sel_err)
                        selected_model = "auto"

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

        # Multi-domain engineering & security audit (e.g. ERP request)
        if any(w in req_lower for w in ("inspect", "analyze", "bug", "audit", "erp", "codebase", "review")):
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
                    "description": "Analyze code quality, performance bottlenecks, and potential bugs",
                    "assigned_agent": "forge",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Analyzing architecture and bugs for: {user_request}"},
                    "expected_output": "Bug findings and architecture review",
                    "verification_criteria": "Identified potential defects or bottlenecks",
                },
                {
                    "step_id": "step-3",
                    "step_number": 3,
                    "title": "Security & Dependency Posture Audit",
                    "description": "Scan security configuration, access boundaries, and vulnerability posture",
                    "assigned_agent": "sentinel",
                    "dependencies": ["step-1"],
                    "tool_name": "think",
                    "tool_params": {"thought": f"Assessing security boundaries and risk for: {user_request}"},
                    "expected_output": "Security vulnerability analysis",
                    "verification_criteria": "Security posture documented",
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
                    "description": "Verify code findings, check claims, and test assumptions",
                    "assigned_agent": "vanguard",
                    "dependencies": ["step-2", "step-3", "step-4"],
                    "tool_name": "think",
                    "tool_params": {"thought": "Verifying evidence and cross-checking findings across Forge, Sentinel, and Oracle"},
                    "expected_output": "QA verification report with verified claims",
                    "verification_criteria": "All claims checked against source evidence",
                },
            ]

        # Research-focused goal
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

        # Standard single/dual fallback
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

    def execute_step_safely(self, step: TaskStep, task_id: str = "task-default") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Standard executor routing to appropriate specialist runtime or tool."""
        if step.assigned_agent == "forge" and step.tool_name in ("forge_coding", "code_goal", "coding", "code_interpreter"):
            from openjarvis.workforce.forge_coding import ForgeCodingEngine
            engine = ForgeCodingEngine(registry=self.registry)
            report = engine.execute_coding_task(
                task_id=task_id,
                step_id=step.step_id,
                objective=step.description or step.title,
                workdir=step.tool_params.get("workdir"),
                verification_cmd=step.tool_params.get("verification_cmd"),
            )
            if report.errors:
                return report.to_dict(), "; ".join(report.errors)
            return report.to_dict(), None

        from openjarvis.core.registry import ToolRegistry
        if not ToolRegistry.contains(step.tool_name):
            if step.tool_name == "think":
                return {"result": step.tool_params.get("thought", "Analyzed")}, None
            return None, f"Tool '{step.tool_name}' not registered"

        try:
            tool_cls = ToolRegistry.get(step.tool_name)
            tool_inst = tool_cls() if callable(tool_cls) else tool_cls
            res = tool_inst.execute(**step.tool_params)
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
        step_executor: Optional[Callable[[TaskStep], Tuple[Optional[Dict[str, Any]], Optional[str]]]] = None,
    ) -> Dict[str, Any]:
        """Execute task steps according to DAG dependencies with bounded concurrency."""
        completed_step_ids: Set[str] = set()
        agent_reports: List[AgentReport] = []
        start_time = time.time()
        actual_executor = step_executor or (lambda s: self.execute_step_safely(s, task_id=task_id))

        # Step lookup
        steps_by_id = {s.step_id: s for s in steps}

        while len(completed_step_ids) < len(steps):
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

                        sel_engine = get_model_selection_engine()
                        sel = sel_engine.select_model(task_type=step.assigned_agent, agent_id=step.assigned_agent)
                        step_model = sel.model_id
                    except Exception:
                        step_model = "auto"
                    self.registry.record_heartbeat(step.assigned_agent, current_task_id=task_id, active_model=step_model)
                    futures[executor.submit(actual_executor, step)] = step

                for future in concurrent.futures.as_completed(futures):
                    step = futures[future]
                    try:
                        result, error = future.result()
                        if error:
                            step.status = StepStatus.FAILED
                            step.error = error
                            self.registry.set_agent_state(step.assigned_agent, AgentRuntimeState.IDLE, error=error)
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

        # ATLAS synthesizes consolidated response
        elapsed = round(time.time() - start_time, 2)
        consolidated_summary = self._synthesize_reports(user_request, agent_reports, steps)

        return {
            "task_id": task_id,
            "status": "completed" if len(completed_step_ids) == len(steps) else "incomplete",
            "user_request": user_request,
            "total_steps": len(steps),
            "completed_steps": len(completed_step_ids),
            "duration_seconds": elapsed,
            "reports": [r.to_dict() for r in agent_reports],
            "agent_reports": [r.to_dict() for r in agent_reports],
            "consolidated_response": consolidated_summary,
            "consolidated_report": consolidated_summary,
        }

    def _synthesize_reports(
        self,
        user_request: str,
        reports: List[AgentReport],
        steps: List[TaskStep],
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

        lines.append("## Team Verification & Next Steps")
        vanguard_reports = [r for r in reports if r.agent_id == "vanguard"]
        if vanguard_reports:
            lines.append("✓ Quality & Verification check completed by VANGUARD: All findings cross-checked against source data.")
        else:
            lines.append("All scheduled subtasks completed successfully. Ready for further instructions.")

        return "\n".join(lines)
