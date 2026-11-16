"""FastAPI routes for desktop browser status, tab inspection, tab reuse, and extension bridge."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from openjarvis.browser.manager import get_browser_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/browser", tags=["browser"])

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_local_request(request: Request) -> bool:
    """Verify request originates from a local loopback interface."""
    client_host = request.client.host if request.client else ""
    if client_host not in LOOPBACK_HOSTS:
        return False

    # Check host header to prevent DNS rebinding or exposure through external reverse proxies
    host_header = request.headers.get("host", "").split(":")[0].lower()
    if host_header and host_header not in ("127.0.0.1", "localhost", "testserver", "::1"):
        return False

    return True


class BrowserOpenRequest(BaseModel):
    destination: str = Field(..., description="Destination URL, website name (e.g. 'linkedin'), or search query")
    explicit_new_tab: bool = Field(False, description="Whether to explicitly force a new tab instead of reusing")
    task_context: Optional[str] = Field("", description="Optional prompt context to infer new-tab or sensitive workflows")


class BrowserActivateRequest(BaseModel):
    window_id: Optional[Union[int, str]] = Field(0, description="Window ID if known")
    title_hint: Optional[str] = Field("", description="Optional title hint for window matching")


@router.get("/status")
def get_browser_status() -> Dict[str, Any]:
    """Retrieve current operational status, connected mode, tab count, and active tab."""
    mgr = get_browser_manager()
    return mgr.get_status()


@router.get("/tabs")
def list_browser_tabs() -> Dict[str, Any]:
    """Retrieve all open browser tabs across running windows."""
    mgr = get_browser_manager()
    tabs = mgr.list_tabs()
    return {
        "count": len(tabs),
        "tabs": [t.to_dict() for t in tabs],
    }


@router.post("/tabs/open")
async def open_or_reuse_tab(req: BrowserOpenRequest) -> Dict[str, Any]:
    """Open or intelligently reuse an existing browser tab using the 6-step decision pipeline asynchronously."""
    mgr = get_browser_manager()
    result = await mgr.open_or_reuse_async(
        query_or_url=req.destination,
        explicit_new_tab=req.explicit_new_tab,
        task_context=req.task_context or "",
    )
    return result.to_dict()


@router.post("/tabs/{tab_id}/activate")
async def activate_tab(tab_id: str, req: Optional[BrowserActivateRequest] = None) -> Dict[str, Any]:
    """Activate a specific browser tab and bring its window to the foreground."""
    mgr = get_browser_manager()
    window_id = req.window_id if req else 0
    title_hint = req.title_hint if req else ""
    success = await mgr.activate_tab_async(tab_id, window_id=window_id or 0, title_hint=title_hint or "")
    return {
        "status": "ok" if success else "failed",
        "activated": success,
        "tab_id": tab_id,
    }


@router.delete("/tabs/{tab_id}")
async def close_tab(tab_id: str) -> Dict[str, Any]:
    """Close an open browser tab."""
    mgr = get_browser_manager()
    success = await mgr.close_tab_async(tab_id)
    return {
        "status": "ok" if success else "failed",
        "closed": success,
        "tab_id": tab_id,
    }


@router.post("/connect")
def connect_or_refresh_browser() -> Dict[str, Any]:
    """Probe running desktop browsers and bridges to refresh connection state."""
    mgr = get_browser_manager()
    mgr.refresh_status()
    return mgr.get_status()


@router.get("/extension/token")
def get_extension_pairing_token(request: Request) -> Dict[str, str]:
    """Return local pairing token for desktop browser extension auto-discovery (loopback only)."""
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Browser extension pairing token is only accessible from local loopback (127.0.0.1).",
        )
    mgr = get_browser_manager()
    return {"token": mgr.extension_bridge.token}


@router.post("/extension/token/regenerate")
def regenerate_extension_pairing_token(request: Request) -> Dict[str, str]:
    """Regenerate pairing token and revoke previous credentials (loopback only)."""
    if not _is_local_request(request):
        raise HTTPException(
            status_code=403,
            detail="Token regeneration is only allowed from local loopback (127.0.0.1).",
        )
    mgr = get_browser_manager()
    new_token = mgr.extension_bridge.regenerate_token()
    return {"token": new_token, "status": "regenerated"}


@router.websocket("/extension/ws")
async def browser_extension_websocket(websocket: WebSocket) -> None:
    """Secure WebSocket endpoint for the companion browser extension."""
    mgr = get_browser_manager()
    bridge = mgr.extension_bridge

    # Restrict connection origin to loopback / extension schemes
    client_host = websocket.client.host if websocket.client else ""
    if client_host not in LOOPBACK_HOSTS:
        logger.warning("Browser extension WebSocket rejected: non-local client %s", client_host)
        await websocket.close(code=1008)
        return

    # Validate Origin header to prevent cross-site websocket hijacking from untrusted web pages
    origin = websocket.headers.get("origin", "").lower()
    if origin and (origin.startswith("http://") or origin.startswith("https://")):
        origin_host = origin.split("://")[1].split("/")[0].split(":")[0]
        if origin_host not in ("localhost", "127.0.0.1", "::1"):
            logger.warning("Browser extension WebSocket rejected: untrusted web origin %s", origin)
            await websocket.close(code=1008)
            return

    token = websocket.query_params.get("token")
    if not bridge.validate_token(token):
        logger.warning("Browser extension WebSocket rejected: unauthorized pairing attempt from %s", client_host)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    bridge.on_ws_connect(websocket, loop=loop)
    logger.info("Browser extension connected via WebSocket.")

    try:
        while True:
            text = await websocket.receive_text()
            bridge.handle_message(text)
    except WebSocketDisconnect:
        logger.info("Browser extension WebSocket disconnected.")
    except Exception as exc:
        logger.debug("Browser extension WebSocket session ended: %s", exc)
    finally:
        bridge.on_ws_disconnect()
