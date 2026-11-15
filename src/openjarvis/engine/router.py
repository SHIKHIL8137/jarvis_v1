"""Dynamic model router and low-VRAM-aware model switching."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Intent regexes for voice/chat model commands
_SWITCH_PATTERN = re.compile(
    r"^(?:please\s+)?(?:switch|change|set)\s+(?:the\s+)?(?:model\s+to|to\s+model|to)\s+([a-zA-Z0-9\.\-_/:]+)",
    re.IGNORECASE,
)
_QUERY_PATTERN = re.compile(
    r"^(?:which|what)\s+model\s+(?:are\s+you\s+using|is\s+active|is\s+selected)|(?:current\s+model)",
    re.IGNORECASE,
)


class ModelRouter:
    """Manages active model selection and dynamic switching with VRAM awareness."""

    def __init__(
        self,
        default_model: Optional[str] = None,
        vram_limit_mb: int = 4096,
        engine: Optional[Any] = None,
    ) -> None:
        if not default_model:
            try:
                from openjarvis.models.selector import ModelSelectionEngine
                res = ModelSelectionEngine.get_instance().select_model(task_type="chat")
                default_model = res.model_name
            except Exception:
                default_model = "auto"
        self.active_model = default_model or "auto"
        self.vram_limit_mb = vram_limit_mb
        self.engine = engine

    def is_model_intent(self, user_input: str) -> Tuple[bool, Optional[str], Optional[str]]:
        """Detect whether input is asking to switch or query model.

        Returns (is_handled, action, target_model).
        action: 'switch' | 'query' | None
        """
        text = user_input.strip()
        match_query = _QUERY_PATTERN.search(text)
        if match_query:
            return True, "query", self.active_model

        match_switch = _SWITCH_PATTERN.search(text)
        if match_switch:
            target = match_switch.group(1).strip().strip(".,?!")
            return True, "switch", target

        return False, None, None

    def switch_model(self, model_name: str) -> Dict[str, Any]:
        """Switch current active model with low-VRAM safeguards."""
        clean_name = model_name.strip()
        if not clean_name:
            return {"success": False, "error": "Model name cannot be empty"}

        old_model = self.active_model
        self.active_model = clean_name
        logger.info("Switched active model from %s to %s", old_model, self.active_model)

        warning = None
        # On 4 GB VRAM devices, warn if user selects huge model locally
        if any(h in clean_name.lower() for h in ("70b", "32b", "14b", "llama3:8b")):
            warning = (
                f"Selected model '{clean_name}' may exceed your 4 GB VRAM limit. "
                "Ensure CPU offloading or quantisation (Q4/Q5) is configured."
            )

        return {
            "success": True,
            "previous_model": old_model,
            "active_model": self.active_model,
            "warning": warning,
            "message": f"Active model switched to {self.active_model}.",
        }

    def list_models(self) -> Dict[str, Any]:
        """List known models from engine or discovery engine without hardcoding."""
        available: List[str] = []
        if self.engine and hasattr(self.engine, "list_models"):
            try:
                available = self.engine.list_models()
            except Exception as exc:
                logger.warning("Failed to list models from engine: %s", exc)

        if not available:
            try:
                from openjarvis.models.discovery import ModelDiscoveryEngine
                discovered = ModelDiscoveryEngine.get_instance().get_all_models()
                if not discovered:
                    discovered = ModelDiscoveryEngine.get_instance().discover_all()
                available = [m.id for m in discovered]
            except Exception as exc:
                logger.warning("Failed to list models from discovery engine: %s", exc)

        if not available and self.active_model:
            available = [self.active_model]

        if self.active_model and self.active_model not in available:
            available.insert(0, self.active_model)

        return {
            "active_model": self.active_model,
            "available_models": sorted(list(set(available))),
            "vram_limit_mb": self.vram_limit_mb,
        }
