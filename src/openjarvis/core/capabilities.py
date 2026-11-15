"""Runtime capability registry and host environment detection for OpenJarvis."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RuntimeCapabilityRegistry:
    """Discovers and exposes real-time hardware, model, voice, and security capabilities."""

    @classmethod
    def detect_hardware(cls) -> Dict[str, Any]:
        """Detect host CPU, RAM, GPU, and OS."""
        hw: Dict[str, Any] = {
            "os": platform.platform(),
            "os_name": platform.system(),
            "cpu_architecture": platform.machine(),
            "cpu_name": platform.processor(),
            "cpu_logical_cores": os.cpu_count() or 1,
            "ram_total_gb": 0.0,
            "ram_available_gb": 0.0,
            "gpu_name": None,
            "gpu_vram_total_mb": 0,
            "gpu_vram_free_mb": 0,
            "gpu_available": False,
        }

        # CPU Name on Windows
        if sys.platform == "win32":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                )
                val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                hw["cpu_name"] = val.strip()
            except Exception:
                pass

        # RAM via psutil
        try:
            import psutil

            mem = psutil.virtual_memory()
            hw["ram_total_gb"] = round(mem.total / (1024**3), 2)
            hw["ram_available_gb"] = round(mem.available / (1024**3), 2)
            hw["ram_percent_used"] = mem.percent
        except ImportError:
            pass

        # GPU via nvidia-smi
        if shutil.which("nvidia-smi"):
            try:
                res = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if res.returncode == 0 and res.stdout.strip():
                    parts = [p.strip() for p in res.stdout.strip().split(",")]
                    if len(parts) >= 3:
                        hw["gpu_name"] = parts[0]
                        hw["gpu_vram_total_mb"] = int(parts[1])
                        hw["gpu_vram_free_mb"] = int(parts[2])
                        hw["gpu_available"] = True
            except Exception as exc:
                logger.debug("nvidia-smi probe failed: %s", exc)

        return hw

    @classmethod
    def get_capabilities(cls, app_state: Optional[Any] = None) -> Dict[str, Any]:
        """Aggregate full system capabilities."""
        hw = cls.detect_hardware()

        # Tools count
        from openjarvis.core.registry import ToolRegistry

        tools_available = sorted(ToolRegistry.keys())

        # Speech status
        voice_manager = getattr(app_state, "voice_session_manager", None) if app_state else None
        speech_info = {
            "supported": True,
            "wake_word_supported": True,
            "wake_phrase": "Hey Jarvis",
            "wake_active": getattr(voice_manager, "state", "idle") == "wake_listening" if voice_manager else False,
            "session_state": getattr(voice_manager, "state", "idle") if voice_manager else "idle",
            "stt_engine": "faster-whisper",
            "tts_engine": "kokoro",
            "echo_guard": True,
        }

        # Background task engine status
        task_engine = getattr(app_state, "task_engine", None) if app_state else None
        task_info = {
            "background_engine_active": task_engine is not None,
            "running_tasks": len(task_engine._running) if task_engine else 0,
            "supported_profiles": ["general", "research", "coding", "monitor"],
        }

        # Security & approval policy
        security_info = {
            "permission_tiers": [
                "read_only",
                "safe_local",
                "reversible_local",
                "sensitive",
                "irreversible",
            ],
            "approval_gateway_enforced": True,
            "fresh_approval_required_for_sensitive": True,
            "action_journal_enabled": True,
            "file_compensation_undo": True,
        }

        return {
            "system": "OpenJarvis",
            "version": "1.1.0-mark-lvii",
            "hardware": hw,
            "tools": {
                "count": len(tools_available),
                "list": tools_available,
            },
            "speech": speech_info,
            "tasks": task_info,
            "security": security_info,
        }
