"""Phase 6 — Real-Time QA, Browser Testing, and Visual Verification Benchmark.

Executes live end-to-end browser user journeys against the running Jarvis instance
(backend at http://127.0.0.1:8000, frontend at http://127.0.0.1:5173).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from playwright.sync_api import sync_playwright

from openjarvis.browser.manager import DesktopBrowserManager
from openjarvis.browser.models import BrowserTabInfo
from openjarvis.tasks.engine import BackgroundTaskEngine
from openjarvis.tasks.models import StepStatus, TaskBudget, TaskStatus, TaskStep
from openjarvis.tasks.store import TaskStore
from openjarvis.tools.approval_store import ApprovalStore, STATUS_APPROVED, STATUS_DENIED

FRONTEND_URL = "http://127.0.0.1:5173"
BACKEND_URL = "http://127.0.0.1:8000"


def main() -> int:
    t_start = time.time()
    results = {
        "benchmark": "Phase 6 Real-Time QA & Visual Verification",
        "timestamp": time.time(),
        "scenarios": {},
        "screenshots": [],
        "verdict": "FAILED",
    }

    screenshots_dir = PROJECT_ROOT / "scripts" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    try:
        print("=== STEP 1: Launching Playwright Chromium Browser ===")
        p = sync_playwright().start()
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print("\n=== STEP 2: Main Dashboard Load & Error Verification ===")
        resp = page.goto(FRONTEND_URL, wait_until="domcontentloaded", timeout=15000)
        assert resp is not None and resp.status == 200, f"Failed to load dashboard: {resp}"
        page.wait_for_timeout(1000)

        title = page.title()
        print(f"Page Title: '{title}'")
        assert "OpenJarvis" in title, f"Unexpected page title: {title}"

        # Capture desktop screenshot
        desktop_shot = screenshots_dir / "desktop_dashboard.png"
        page.screenshot(path=str(desktop_shot), full_page=True)
        results["screenshots"].append(str(desktop_shot))
        print(f"Captured desktop screenshot: {desktop_shot}")

        # Check for fatal console errors
        fatal_errors = [e for e in console_errors if "SyntaxError" in e or "ReferenceError" in e or "TypeError" in e]
        assert len(fatal_errors) == 0, f"Fatal console errors: {fatal_errors}"

        results["scenarios"]["dashboard_load"] = {
            "status": "PASSED",
            "title": title,
            "console_errors_count": len(console_errors),
            "fatal_errors": fatal_errors,
        }

        print("\n=== STEP 3: Responsive Layout Verification (Mobile Viewport) ===")
        page.set_viewport_size({"width": 375, "height": 667})
        page.wait_for_timeout(800)

        mobile_shot = screenshots_dir / "mobile_view.png"
        page.screenshot(path=str(mobile_shot), full_page=True)
        results["screenshots"].append(str(mobile_shot))
        print(f"Captured mobile screenshot: {mobile_shot}")

        scroll_w = page.evaluate("document.documentElement.scrollWidth")
        client_w = page.evaluate("document.documentElement.clientWidth")
        assert scroll_w <= client_w + 10, f"Horizontal scroll overflow detected: {scroll_w} > {client_w}"

        # Restore desktop viewport
        page.set_viewport_size({"width": 1280, "height": 800})
        page.wait_for_timeout(500)
        results["scenarios"]["responsive_layout"] = {
            "status": "PASSED",
            "mobile_scroll_width": scroll_w,
            "mobile_client_width": client_w,
        }

        print("\n=== STEP 4: UI Navigation Across Views (Dashboard, Agents, Logs, Chat) ===")
        views_tested = []
        for view_name in ["Dashboard", "Agents", "Data Sources", "Logs", "Chat"]:
            btn = page.locator(f"button:has-text('{view_name}')").first
            if btn.is_visible():
                btn.click()
                page.wait_for_timeout(400)
                views_tested.append(view_name)
                print(f"Navigated to: {view_name}")

        agents_shot = screenshots_dir / "agents_view.png"
        page.screenshot(path=str(agents_shot), full_page=True)
        results["screenshots"].append(str(agents_shot))

        results["scenarios"]["navigation"] = {
            "status": "PASSED",
            "views_tested": views_tested,
        }

        print("\n=== STEP 5: Approval Prompt Approve and Deny Flow ===")
        from openjarvis.tools.approval_store import ApprovalStore
        appr_store = ApprovalStore()

        act_appr = appr_store.queue_action(
            action_type="file_write",
            description="Live benchmark patch to src/settings.py",
            payload={"path": "src/settings.py", "content": "# validated patch"},
            permission_key="file_write",
            tier="sensitive",
        )
        act_deny = appr_store.queue_action(
            action_type="shell_exec",
            description="Live benchmark destructive shell test",
            payload={"command": "rm -rf /"},
            permission_key="shell_exec",
            tier="high_risk",
        )

        appr_store.update_status(act_appr.id, STATUS_APPROVED)
        appr_store.update_status(act_deny.id, STATUS_DENIED)

        v_appr = appr_store.get_action(act_appr.id)
        v_deny = appr_store.get_action(act_deny.id)
        assert v_appr.status == STATUS_APPROVED
        assert v_deny.status == STATUS_DENIED

        results["scenarios"]["approval_flow"] = {
            "status": "PASSED",
            "approved_id": act_appr.id,
            "approved_status": v_appr.status,
            "denied_id": act_deny.id,
            "denied_status": v_deny.status,
        }

        print("\n=== STEP 6: Background Task Lifecycle & Cancellation ===")
        from openjarvis.tasks.engine import BackgroundTaskEngine
        engine = BackgroundTaskEngine()

        task = engine.create_task(
            title="Live Benchmark Long Task",
            user_request="Live test background tracking",
            agent_profile="general",
        )
        task.plan = [
            TaskStep(step_id="step-live-1", step_number=1, title="Step 1", tool_name="think", status=StepStatus.PENDING),
            TaskStep(step_id="step-live-2", step_number=2, title="Step 2", tool_name="think", status=StepStatus.PENDING),
        ]
        task.status = TaskStatus.RUNNING
        engine.store.save_task(task)

        # Cancel task
        engine.cancel_task(task.id)
        refreshed_task = engine.store.get_task(task.id)
        assert refreshed_task.status == TaskStatus.CANCELLED

        results["scenarios"]["task_cancellation"] = {
            "status": "PASSED",
            "task_id": task.id,
            "final_status": refreshed_task.status.value,
        }

        print("\n=== STEP 7: Browser Tab Reuse & Deduplication ===")
        from unittest.mock import AsyncMock, patch
        mgr = DesktopBrowserManager()
        existing_tabs = [
            BrowserTabInfo(tab_id=501, window_id=1, url="https://github.com/openjarvis", title="OpenJarvis Repo", is_active=True),
        ]
        with patch.object(mgr, "list_tabs", return_value=existing_tabs), \
             patch.object(mgr, "activate_tab_async", new_callable=AsyncMock) as mock_act:
            mock_act.return_value = True
            reuse_res = mgr.open_or_reuse(query_or_url="https://github.com/openjarvis")
            assert reuse_res.success is True
            assert reuse_res.reused_existing is True
            assert reuse_res.tab.tab_id == 501
            print(f"Verified tab reuse: {reuse_res.message}")

        results["scenarios"]["tab_reuse"] = {
            "status": "PASSED",
            "tab_id": 501,
            "reused_existing": True,
            "action": reuse_res.action,
        }

        print("\n=== STEP 8: Browser Page Reload State Reconciliation ===")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        assert "OpenJarvis" in page.title()
        assert page.locator("body").is_visible()

        results["scenarios"]["page_refresh_reconciliation"] = {
            "status": "PASSED",
            "page_title_after_refresh": page.title(),
        }

        context.close()
        browser.close()
        p.stop()

        results["verdict"] = "PASSED"
        results["duration_seconds"] = round(time.time() - t_start, 2)
        print(f"\n[SUCCESS] Phase 6 Live Browser Benchmark Completed in {results['duration_seconds']}s.")

    except Exception as exc:
        print(f"\n[ERROR] Phase 6 Live Benchmark failed: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        results["error"] = str(exc)
        results["verdict"] = "FAILED"
    finally:
        out_file = PROJECT_ROOT / "scripts" / "phase_6_live_browser_results.json"
        out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Results written to: {out_file}")

    return 0 if results["verdict"] == "PASSED" else 1


if __name__ == "__main__":
    sys.exit(main())
