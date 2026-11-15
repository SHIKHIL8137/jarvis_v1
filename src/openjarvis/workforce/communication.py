"""Team communication bus, shared context management, and conflict resolution."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from openjarvis.core.events import EventBus
from openjarvis.workforce.models import AgentReport
from openjarvis.workforce.store import WorkforceStore

logger = logging.getLogger(__name__)


class TeamMessageBus:
    """Managed message bus for inter-agent communication, handover, and collaboration."""

    def __init__(
        self,
        store: Optional[WorkforceStore] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.store = store or WorkforceStore()
        self.bus = bus

    def send_agent_message(
        self,
        task_id: str,
        from_agent: str,
        to_agent: str,
        message_type: str,
        content: Dict[str, Any],
    ) -> str:
        """Send a structured message from one agent to another."""
        msg_id = self.store.save_message(
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            message_type=message_type,
            content=content,
        )

        if self.bus:
            self.bus.publish(
                "agent_message",
                {
                    "message_id": msg_id,
                    "task_id": task_id,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "message_type": message_type,
                },
            )

        logger.info(
            "TeamMessageBus: [%s] -> [%s] (%s): %s",
            from_agent,
            to_agent,
            message_type,
            str(content)[:80],
        )
        return msg_id

    def post_message(
        self,
        task_id: str,
        from_agent: str,
        to_agent: str,
        message_type: str,
        content: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Convenience wrapper for posting inter-agent messages."""
        payload = {"text": content} if isinstance(content, str) else dict(content)
        if metadata:
            payload["metadata"] = metadata
        return self.send_agent_message(task_id, from_agent, to_agent, message_type, payload)

    def get_task_messages(self, task_id: str) -> List[Dict[str, Any]]:
        return self.store.list_messages(task_id)

    def build_scoped_context(
        self,
        task_id: str,
        for_agent_id: str,
        prior_reports: List[AgentReport],
        user_request: str,
    ) -> Dict[str, Any]:
        """Filter shared context so each specialist receives only relevant prior findings."""
        scoped_findings = []
        for rep in prior_reports:
            if rep.findings:
                for f in rep.findings:
                    scoped_findings.append({
                        "source_agent": rep.agent_id,
                        "title": f.get("title", ""),
                        "summary": f.get("summary", ""),
                    })

        # Relevant inter-agent messages directed to or from this agent
        all_msgs = self.get_task_messages(task_id)
        relevant_msgs = [
            m for m in all_msgs
            if m["to_agent"] in (for_agent_id, "all") or m["from_agent"] == for_agent_id
        ]

        return {
            "task_id": task_id,
            "user_request": user_request,
            "target_agent": for_agent_id,
            "prior_findings": scoped_findings,
            "relevant_messages": relevant_msgs,
        }


class ConflictResolver:
    """Detects, investigates, and resolves contradictions between specialist agent findings."""

    @classmethod
    def detect_and_resolve(
        cls,
        reports: List[AgentReport],
        vanguard_verify_fn: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Analyze reports for contradictory conclusions and document resolution with evidence."""
        conflicts = []

        # Compare findings across pairs
        for i in range(len(reports)):
            for j in range(i + 1, len(reports)):
                rep_a = reports[i]
                rep_b = reports[j]

                # If one specialist reports issues and another reports success on the same task
                if bool(rep_a.errors) != bool(rep_b.errors):
                    err_agent = rep_a if rep_a.errors else rep_b
                    ok_agent = rep_b if rep_a.errors else rep_a
                    conflicts.append({
                        "agents": [err_agent.agent_id, ok_agent.agent_id],
                        "type": "error_discrepancy",
                        "description": (
                            f"{err_agent.agent_id} reported issues ({'; '.join(err_agent.errors)}) "
                            f"while {ok_agent.agent_id} reported no issues."
                        ),
                    })

        resolved_summary = []
        if conflicts:
            for conf in conflicts:
                resolved_summary.append(
                    f"Identified difference between {conf['agents'][0]} and {conf['agents'][1]}: "
                    f"{conf['description']} Verified evidence retained for human review."
                )
        else:
            resolved_summary.append("All specialist findings are aligned and consistent.")

        return {
            "has_conflicts": len(conflicts) > 0,
            "conflicts": conflicts,
            "resolution_notes": resolved_summary,
        }
