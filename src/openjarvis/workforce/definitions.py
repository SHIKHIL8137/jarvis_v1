"""Predefined specialist agent manifests and custom manifest loader for the workforce."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

from openjarvis.core.paths import get_config_dir
from openjarvis.workforce.models import AgentManifest

logger = logging.getLogger(__name__)

INITIAL_WORKFORCE: List[AgentManifest] = [
    # 1. ATLAS - Chief of Staff / Coordinator
    AgentManifest(
        id="atlas",
        display_name="Atlas",
        role="Chief of Staff / Coordinator",
        description=(
            "Coordinates the multi-agent engineering and operations team. Deconstructs user requests into "
            "structured task DAGs, delegates subtasks, monitors progress, and synthesizes consolidated reports."
        ),
        system_instructions=(
            "You are ATLAS, the Chief of Staff and Team Coordinator for OpenJarvis. You act as the team commander. "
            "Your job is to understand the user's ultimate goal, decompose complex requests into discrete subtasks, "
            "assign the appropriate specialist agents (Oracle, Forge, Sentinel, Scout, Vanguard, Pulse), coordinate "
            "parallel execution, resolve dependencies, and compile a single authoritative, cohesive report for the user."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["think", "knowledge_search"],
        allowed_capabilities=["planning", "coordination", "synthesis"],
        max_execution_time_seconds=600,
        max_tool_calls=30,
        avatar="shield",
    ),
    # 2. ORACLE - Research Specialist
    AgentManifest(
        id="oracle",
        display_name="Oracle",
        role="Research Specialist",
        description=(
            "Conducts deep research, technical comparisons, evidence extraction, citation validation, "
            "and generates evidence-backed markdown research reports."
        ),
        system_instructions=(
            "You are ORACLE, the Research Specialist. You investigate technical questions, evaluate technologies, "
            "retrieve authoritative web and document sources, extract verified quotations, and write comprehensive "
            "evidence-backed research reports. You strictly verify every cited claim against raw source text."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["knowledge_search", "think", "file_read"],
        denied_tools=["shell_exec", "file_write", "desktop", "channel_send"],
        allowed_capabilities=["research", "web_read", "evidence_extraction"],
        denied_capabilities=["file:write", "code:execute", "system:admin"],
        max_execution_time_seconds=600,
        max_tool_calls=40,
        avatar="search",
    ),
    # 3. FORGE - Software Engineering Specialist
    AgentManifest(
        id="forge",
        display_name="Forge",
        role="Software Engineering Specialist",
        description=(
            "Inspects codebases, analyzes architecture, investigates bugs, writes tests, and implements "
            "features with strict adherence to permission and approval gates."
        ),
        system_instructions=(
            "You are FORGE, the Software Engineering Specialist. You inspect project structures, analyze bugs, "
            "design software architectures, write unit tests, and implement verified code changes. You operate strictly "
            "within permitted workspace folders and never bypass the approval gateway for code modifications."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="claude-3-5-sonnet-20241022",
        allowed_tools=["file_read", "file_write", "shell_exec", "git_status", "git_diff", "think"],
        denied_tools=["desktop", "channel_send"],
        allowed_capabilities=["file:read", "file:write", "code:execute"],
        max_execution_time_seconds=900,
        max_tool_calls=50,
        avatar="terminal",
    ),
    # 4. SENTINEL - Security and Infrastructure Specialist
    AgentManifest(
        id="sentinel",
        display_name="Sentinel",
        role="Security and Infrastructure Specialist",
        description=(
            "Audits security posture, detects vulnerabilities, scans dependencies, inspects infrastructure "
            "configurations, and performs permission governance."
        ),
        system_instructions=(
            "You are SENTINEL, the Security and Infrastructure Specialist. You perform security assessments, "
            "inspect configuration boundaries, analyze dependencies for known vulnerabilities, verify permission "
            "isolation, and flag security risks before any sensitive operations proceed."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["file_read", "think", "knowledge_search"],
        denied_tools=["desktop", "channel_send"],
        allowed_capabilities=["security:audit", "file:read"],
        denied_capabilities=["file:write", "channel:send"],
        max_execution_time_seconds=300,
        max_tool_calls=25,
        avatar="lock",
    ),
    # 5. SCOUT - File and Knowledge Specialist
    AgentManifest(
        id="scout",
        display_name="Scout",
        role="File and Knowledge Specialist",
        description=(
            "Discovers local files, parses documents (PDF, text, markdown), retrieves memory facts, "
            "and maps repository structures."
        ),
        system_instructions=(
            "You are SCOUT, the File and Knowledge Specialist. You explore local directory trees, locate relevant "
            "documentation and code files, analyze document contents, search long-term memory facts, and supply structured "
            "context to the rest of the team."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["file_read", "knowledge_search", "think"],
        denied_tools=["shell_exec", "file_write", "desktop", "channel_send"],
        allowed_capabilities=["file:read", "memory:read"],
        denied_capabilities=["file:write", "code:execute", "channel:send"],
        max_execution_time_seconds=300,
        max_tool_calls=30,
        avatar="folder",
    ),
    # 6. VANGUARD - QA and Verification Specialist
    AgentManifest(
        id="vanguard",
        display_name="Vanguard",
        role="QA and Verification Specialist",
        description=(
            "Executes automated tests, reviews code changes, validates research claims against raw sources, "
            "and verifies acceptance criteria."
        ),
        system_instructions=(
            "You are VANGUARD, the QA and Verification Specialist. You critically review the work of other agents. "
            "You execute tests, verify that claims are supported by authoritative evidence, inspect proposed diffs, "
            "and flag inconsistencies or regressions before final reporting."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["file_read", "shell_exec", "think"],
        denied_tools=["desktop", "channel_send"],
        allowed_capabilities=["file:read", "code:execute", "verification"],
        denied_capabilities=["file:write", "channel:send"],
        max_execution_time_seconds=400,
        max_tool_calls=30,
        avatar="check-circle",
    ),
    # 7. PULSE - Operations and Productivity Specialist
    AgentManifest(
        id="pulse",
        display_name="Pulse",
        role="Operations and Productivity Specialist",
        description=(
            "Coordinates routines, reminders, system health monitoring, background scheduling, "
            "and operational notifications respecting quiet hours."
        ),
        system_instructions=(
            "You are PULSE, the Operations and Productivity Specialist. You manage scheduled routines, trigger periodic "
            "health checks, coordinate reminders, monitor background services, and respect quiet hours for unsolicited "
            "announcements."
        ),
        model_preference="qwen2.5:3b",
        model_fallback="gpt-4o-mini",
        allowed_tools=["think", "knowledge_search"],
        allowed_capabilities=["routines", "monitoring", "operations"],
        max_execution_time_seconds=300,
        max_tool_calls=20,
        avatar="activity",
    ),
]


def load_manifests_from_disk() -> Dict[str, AgentManifest]:
    """Load default manifests and overlay any user-defined manifests in ~/.openjarvis/agents/."""
    manifests: Dict[str, AgentManifest] = {m.id: m for m in INITIAL_WORKFORCE}

    agents_dir = get_config_dir() / "agents"
    if not agents_dir.exists():
        return manifests

    for path in agents_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = AgentManifest.from_dict(data)
            manifests[manifest.id] = manifest
            logger.info("Loaded custom agent manifest: %s (%s)", manifest.id, path.name)
        except Exception as exc:
            logger.warning("Failed to load agent manifest from %s: %s", path, exc)

    return manifests
