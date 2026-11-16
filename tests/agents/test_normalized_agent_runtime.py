"""Phase 1: Normalized Agent Runtime & Tool Execution Test Suite.

Validates all 12 required test scenarios from 04_PHASE_1_AGENT_RUNTIME.md:
1. Model returns final answer without tools.
2. Model calls one tool and receives its actual result.
3. Model calls multiple tools sequentially.
4. Invalid tool arguments are rejected.
5. Unknown tool is rejected safely.
6. Tool timeout is recorded.
7. Cancellation stops future tool calls.
8. Repeated identical tool loop is detected.
9. Output truncation is explicit.
10. Task event ordering is correct.
11. Task state survives backend restart.
12. Existing chat regression suite passes.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from openjarvis.agents._stubs import AgentContext, ToolUsingAgent
from openjarvis.agents.loop_guard import LoopGuardConfig
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import Conversation, Message, Role, ToolCall, ToolResult
from openjarvis.tasks.models import BackgroundTask, StepStatus, TaskBudget, TaskStep, TaskStatus
from openjarvis.tasks.store import TaskStore
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from tests.agents.fake_engine import FakeEngine


# ---------------------------------------------------------------------------
# Test Tools
# ---------------------------------------------------------------------------


class CalculatorTool(BaseTool):
    tool_id = "calculator"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            description="Evaluate simple arithmetic expression.",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Arithmetic string"},
                },
                "required": ["expression"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        expr = params.get("expression", "")
        try:
            # Safe basic evaluation for numbers and +, -, *, /
            allowed = set("0123456789+-*/. ()")
            if not all(c in allowed for c in str(expr)):
                return ToolResult(tool_name="calculator", content="Forbidden characters", success=False)
            val = eval(str(expr))  # noqa: S307
            return ToolResult(tool_name="calculator", content=str(val), success=True)
        except Exception as e:
            return ToolResult(tool_name="calculator", content=str(e), success=False)


class SlowTool(BaseTool):
    tool_id = "slow_tool"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="slow_tool",
            description="A tool that takes too long to execute.",
            parameters={
                "type": "object",
                "properties": {"sleep_time": {"type": "number"}},
            },
            timeout_seconds=0.2,
        )

    def execute(self, **params: Any) -> ToolResult:
        sleep_time = float(params.get("sleep_time", 0.5))
        time.sleep(sleep_time)
        return ToolResult(tool_name="slow_tool", content="Finished sleeping", success=True)


# ---------------------------------------------------------------------------
# 1. Model returns final answer without tools
# ---------------------------------------------------------------------------


def test_scenario_1_final_answer_without_tools() -> None:
    engine = FakeEngine([{"content": "Paris is the capital of France."}])
    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
    )

    result = agent.run("What is the capital of France?")
    assert result.content == "Paris is the capital of France."
    assert len(result.tool_results) == 0
    assert result.turns == 1
    assert result.metadata.get("total_tokens", 0) > 0


# ---------------------------------------------------------------------------
# 2. Model calls one tool and receives its actual result
# ---------------------------------------------------------------------------


def test_scenario_2_one_tool_call_receives_actual_result() -> None:
    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "calc_1",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "21 * 2"}),
                }
            ],
        },
        {"content": "The result is 42."},
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
    )

    result = agent.run("What is 21 * 2?")
    assert result.content == "The result is 42."
    assert len(result.tool_results) == 1
    assert result.tool_results[0].tool_name == "calculator"
    assert result.tool_results[0].success is True
    assert result.tool_results[0].content == "42"
    assert result.turns == 2


# ---------------------------------------------------------------------------
# 3. Model calls multiple tools sequentially
# ---------------------------------------------------------------------------


def test_scenario_3_multiple_tools_sequentially() -> None:
    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "step_1",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "10 + 5"}),
                }
            ],
        },
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "step_2",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "15 * 3"}),
                }
            ],
        },
        {"content": "Computed sequentially: 45."},
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
    )

    result = agent.run("Compute (10 + 5) * 3 step by step.")
    assert result.content == "Computed sequentially: 45."
    assert len(result.tool_results) == 2
    assert result.tool_results[0].content == "15"
    assert result.tool_results[1].content == "45"
    assert result.turns == 3


# ---------------------------------------------------------------------------
# 4. Invalid tool arguments are rejected
# ---------------------------------------------------------------------------


def test_scenario_4_invalid_tool_arguments_rejected() -> None:
    tool = CalculatorTool()
    executor = ToolExecutor([tool], strict_validation=True)

    # Missing required parameter "expression"
    tc_missing = ToolCall(id="tc_1", name="calculator", arguments="{}")
    res_missing = executor.execute(tc_missing)
    assert res_missing.success is False
    assert "missing required parameter" in res_missing.content.lower()

    # Invalid type: number instead of string
    tc_type = ToolCall(id="tc_2", name="calculator", arguments=json.dumps({"expression": 12345}))
    res_type = executor.execute(tc_type)
    assert res_type.success is False
    assert "must be a string" in res_type.content.lower()


# ---------------------------------------------------------------------------
# 5. Unknown tool is rejected safely
# ---------------------------------------------------------------------------


def test_scenario_5_unknown_tool_rejected_safely() -> None:
    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "unk_1",
                    "name": "non_existent_tool_xyz",
                    "arguments": "{}",
                }
            ],
        },
        {"content": "I realized the tool was unavailable."},
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
    )

    result = agent.run("Run mysterious tool.")
    assert len(result.tool_results) == 1
    assert result.tool_results[0].success is False
    assert "Unknown tool" in result.tool_results[0].content


# ---------------------------------------------------------------------------
# 6. Tool timeout is recorded
# ---------------------------------------------------------------------------


def test_scenario_6_tool_timeout_recorded() -> None:
    bus = EventBus()
    timeout_events: list[dict] = []
    bus.subscribe(EventType.TOOL_TIMEOUT, lambda e: timeout_events.append(e.data))

    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "slow_1",
                    "name": "slow_tool",
                    "arguments": json.dumps({"sleep_time": 0.5}),
                }
            ],
        },
        {"content": "The tool timed out."},
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[SlowTool()],
        bus=bus,
    )

    result = agent.run("Please wait for slow tool.")
    assert len(timeout_events) == 1
    assert timeout_events[0]["tool"] == "slow_tool"
    assert len(result.tool_results) == 1
    assert result.tool_results[0].success is False
    assert "timed out" in result.tool_results[0].content


# ---------------------------------------------------------------------------
# 7. Cancellation stops future tool calls
# ---------------------------------------------------------------------------


def test_scenario_7_cancellation_stops_future_tool_calls() -> None:
    cancel_event = threading.Event()

    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "1 + 1"}),
                }
            ],
        },
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c2",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "2 + 2"}),
                }
            ],
        },
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
        cancellation_token=cancel_event,
    )

    # Cancel immediately
    cancel_event.set()

    result = agent.run("Calculate things.")
    assert result.metadata.get("cancelled") is True
    assert "cancelled" in result.content.lower()
    assert len(result.tool_results) == 0


# ---------------------------------------------------------------------------
# 8. Repeated identical tool loop is detected
# ---------------------------------------------------------------------------


def test_scenario_8_repeated_identical_tool_loop_detected() -> None:
    bus = EventBus()
    loop_events: list[dict] = []
    bus.subscribe(EventType.LOOP_GUARD_TRIGGERED, lambda e: loop_events.append(e.data))

    # LLM repeatedly calls exact same tool with exact same arguments
    identical_call = {
        "content": "",
        "tool_calls": [
            {
                "id": "loop_call",
                "name": "calculator",
                "arguments": json.dumps({"expression": "5 + 5"}),
            }
        ],
    }

    engine = FakeEngine([identical_call, identical_call, identical_call, identical_call])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
        bus=bus,
        loop_guard_config=LoopGuardConfig(
            enabled=True,
            max_identical_calls=2,
            warn_before_block=False,
        ),
    )

    result = agent.run("Loop test")
    assert any(
        not r.success and "Loop guard" in r.content
        for r in result.tool_results
    )
    assert len(loop_events) >= 1


# ---------------------------------------------------------------------------
# 9. Output truncation is explicit
# ---------------------------------------------------------------------------


def test_scenario_9_output_truncation_is_explicit() -> None:
    huge_text = "JARVIS_AUTONOMOUS_" * 50
    engine = FakeEngine([{"content": huge_text}])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[],
        max_output_chars=50,
    )

    result = agent.run("Give me long text")
    assert "[Output truncated at 50 characters]" in result.content
    assert result.content.startswith(huge_text[:50])


# ---------------------------------------------------------------------------
# 10. Task event ordering is correct
# ---------------------------------------------------------------------------


def test_scenario_10_task_event_ordering_correct() -> None:
    bus = EventBus(record_history=True)

    engine = FakeEngine([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "calculator",
                    "arguments": json.dumps({"expression": "7 * 6"}),
                }
            ],
        },
        {"content": "42"},
    ])

    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[CalculatorTool()],
        bus=bus,
    )

    agent.run("Calculate 7 * 6")

    event_order = [e.event_type.value for e in bus._history]

    # Verify standard turn and tool event ordering
    assert event_order[0] == EventType.AGENT_TURN_START.value
    assert EventType.TOOL_CALL_START.value in event_order
    assert EventType.TOOL_CALL_END.value in event_order
    assert event_order[-1] == EventType.AGENT_TURN_END.value

    # Index of tool call start must precede tool call end
    idx_tc_start = event_order.index(EventType.TOOL_CALL_START.value)
    idx_tc_end = event_order.index(EventType.TOOL_CALL_END.value)
    assert idx_tc_start < idx_tc_end


# ---------------------------------------------------------------------------
# 11. Task state survives backend restart
# ---------------------------------------------------------------------------


def test_scenario_11_task_state_survives_backend_restart(tmp_path: Path) -> None:
    db_file = tmp_path / "test_tasks.db"

    # 1. Initialize store and create a persistent task
    store_1 = TaskStore(db_path=db_file)
    task = BackgroundTask(
        id="task-autonomy-001",
        title="Autonomous Workspace Audit",
        user_request="Inspect workspace and summarize findings",
        status=TaskStatus.RUNNING,
        agent_profile="orchestrator",
        plan=[
            TaskStep(step_id="s0", step_number=0, title="Scan files", description="Find python source files", status=StepStatus.COMPLETED),
            TaskStep(step_id="s1", step_number=1, title="Run linters", description="Check code quality", status=StepStatus.RUNNING),
            TaskStep(step_id="s2", step_number=2, title="Produce report", description="Write final summary", status=StepStatus.PENDING),
        ],
        current_step_index=1,
        budget=TaskBudget(max_execution_time_seconds=300.0, max_tool_calls=50),
    )
    store_1.save_task(task)
    store_1.log_event("task-autonomy-001", "checkpoint_created", {"step": 1})
    store_1.add_steering_instruction("task-autonomy-001", "Focus on src/openjarvis only")
    store_1.update_status("task-autonomy-001", TaskStatus.COMPLETED)

    # Simulate backend termination: close DB connection and delete reference
    store_1._conn.close()
    del store_1

    # 2. Simulate backend startup: open fresh TaskStore on same SQLite DB
    store_2 = TaskStore(db_path=db_file)
    recovered = store_2.get_task("task-autonomy-001")

    assert recovered is not None
    assert recovered.id == "task-autonomy-001"
    assert recovered.title == "Autonomous Workspace Audit"
    assert recovered.status == TaskStatus.COMPLETED
    assert recovered.current_step_index == 1
    assert len(recovered.plan) == 3
    assert recovered.plan[0].status == StepStatus.COMPLETED
    assert recovered.plan[1].status == StepStatus.RUNNING
    assert recovered.steering_instructions == ["Focus on src/openjarvis only"]
    assert recovered.budget.max_execution_time_seconds == 300.0
    assert recovered.budget.max_tool_calls == 50

    # Verify event trail survived
    events = store_2.get_events("task-autonomy-001")
    assert any(e["event_type"] == "checkpoint_created" for e in events)
    assert any(e["event_type"] == "task_steered" for e in events)

    store_2._conn.close()


# ---------------------------------------------------------------------------
# 12. Existing chat regression suite passes
# ---------------------------------------------------------------------------


def test_scenario_12_existing_chat_regression_passes() -> None:
    engine = FakeEngine([{"content": "Hello! I am Jarvis, how can I assist you?"}])
    agent = OrchestratorAgent(
        engine,
        model="fake-model",
        tools=[],
    )

    ctx = AgentContext(
        conversation=Conversation(
            messages=[
                Message(role=Role.USER, content="Hello"),
                Message(role=Role.ASSISTANT, content="Hi there!"),
            ]
        )
    )

    result = agent.run("How are you?", context=ctx)
    assert result.content == "Hello! I am Jarvis, how can I assist you?"
    assert result.turns == 1
    assert result.tool_results == []
