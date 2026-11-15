"""Dynamic Model Discovery Engine — probes local and cloud providers for available models and capabilities."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set

from openjarvis.core.config import JarvisConfig, load_config
from openjarvis.models.capabilities import ModelCapabilityInfo

logger = logging.getLogger(__name__)

# Known coding model pattern keywords
_CODING_PATTERNS = re.compile(r"(coder|code|starcoder|codellama|codestral|granite.*code)", re.IGNORECASE)
# Known reasoning / thinking model pattern keywords
_REASONING_PATTERNS = re.compile(r"(reason|think|deepseek-r1|qwq|\bo1\b|\bo3\b|\br1\b)", re.IGNORECASE)
# Known vision / multimodal model pattern keywords
_VISION_PATTERNS = re.compile(r"(vision|vl|omni|\b4o\b|gemini|fuyu|kosmos|deplot|neva)", re.IGNORECASE)


class ModelDiscoveryEngine:
    """Central registry and dynamic discovery mechanism for local and cloud models."""

    _instance: Optional[ModelDiscoveryEngine] = None
    _instance_lock = threading.Lock()

    def __init__(self, config: Optional[JarvisConfig] = None) -> None:
        self.config = config or load_config()
        self._lock = threading.RLock()
        self._discovered_models: Dict[str, ModelCapabilityInfo] = {}
        self._provider_health: Dict[str, Dict[str, Any]] = {}
        self._last_refresh_time: float = 0.0
        self._cache_ttl_seconds: float = 180.0  # 3 minutes

    @classmethod
    def get_instance(cls, config: Optional[JarvisConfig] = None) -> ModelDiscoveryEngine:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(config=config)
            return cls._instance

    def discover_all(self, force: bool = False) -> List[ModelCapabilityInfo]:
        """Discover models from all configured and reachable providers with caching."""
        with self._lock:
            now = time.time()
            if not force and self._discovered_models and (now - self._last_refresh_time) < self._cache_ttl_seconds:
                return list(self._discovered_models.values())

            discovered: Dict[str, ModelCapabilityInfo] = {}

            # 1. Probe local engines
            local_models = self._probe_local_engines()
            for m in local_models:
                discovered[m.model_id] = m

            # 2. Probe authenticated cloud engines
            cloud_models = self._probe_cloud_engines()
            for m in cloud_models:
                discovered[m.model_id] = m

            self._discovered_models = discovered
            self._last_refresh_time = now
            logger.info("ModelDiscoveryEngine: Discovered %d available models across providers", len(discovered))
            return list(discovered.values())

    def get_model_info(self, model_id: str) -> Optional[ModelCapabilityInfo]:
        """Look up metadata and capabilities of a specific discovered model."""
        with self._lock:
            if not self._discovered_models:
                self.discover_all()
            # Exact match
            if model_id in self._discovered_models:
                return self._discovered_models[model_id]
            # Match without provider prefix or case-insensitive
            for m in self._discovered_models.values():
                if m.model_id.lower() == model_id.lower() or m.model_id.split("/")[-1].lower() == model_id.lower():
                    return m
            return None

    def get_all_models(self) -> List[ModelCapabilityInfo]:
        """Return all discovered models, running discovery if catalog is empty."""
        with self._lock:
            if not self._discovered_models:
                return self.discover_all()
            return list(self._discovered_models.values())

    def list_providers(self) -> List[Dict[str, Any]]:
        """Return connectivity and health status of all model providers."""
        with self._lock:
            if not self._provider_health:
                self.discover_all()
            return list(self._provider_health.values())

    # --- Local Provider Probes ---

    def _probe_local_engines(self) -> List[ModelCapabilityInfo]:
        models: List[ModelCapabilityInfo] = []
        try:
            from openjarvis.engine._discovery import discover_engines

            engines = discover_engines(self.config)
            for engine_key, engine_inst in engines:
                # Disregard cloud in local probe (cloud handled separately)
                if engine_key in ("cloud", "litellm"):
                    continue

                t0 = time.monotonic()
                try:
                    engine_models = engine_inst.list_models()
                    latency_ms = round((time.monotonic() - t0) * 1000, 1)
                    self._provider_health[engine_key] = {
                        "provider": engine_key,
                        "type": "local",
                        "status": "healthy",
                        "model_count": len(engine_models),
                        "latency_ms": latency_ms,
                        "last_checked": time.time(),
                        "error": None,
                    }

                    for m_name in engine_models:
                        cap = self._analyze_model_capability(
                            model_id=m_name,
                            provider=engine_key,
                            is_local=True,
                            latency_ms=latency_ms,
                        )
                        models.append(cap)
                except Exception as exc:
                    self._provider_health[engine_key] = {
                        "provider": engine_key,
                        "type": "local",
                        "status": "offline",
                        "model_count": 0,
                        "latency_ms": 0.0,
                        "last_checked": time.time(),
                        "error": str(exc),
                    }
        except Exception as exc:
            logger.debug("Local engines probe error: %s", exc)

        # Direct probe for Ollama if not caught in discover_engines
        if "ollama" not in self._provider_health:
            models.extend(self._probe_ollama_direct())

        # Direct probe for LM Studio if reachable on 1234
        if "lmstudio" not in self._provider_health:
            models.extend(self._probe_lmstudio_direct())

        return models

    def _probe_ollama_direct(self) -> List[ModelCapabilityInfo]:
        models: List[ModelCapabilityInfo] = []
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}"

        t0 = time.monotonic()
        try:
            req = urllib.request.Request(f"{host}/api/tags", headers={"User-Agent": "OpenJarvis"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                raw_models = data.get("models", [])
                self._provider_health["ollama"] = {
                    "provider": "ollama",
                    "type": "local",
                    "status": "healthy",
                    "model_count": len(raw_models),
                    "latency_ms": latency_ms,
                    "last_checked": time.time(),
                    "error": None,
                }
                for m in raw_models:
                    name = m.get("name", "")
                    details = m.get("details", {})
                    caps = m.get("capabilities") or []
                    param_size = self._parse_param_size(str(details.get("parameter_size", "")))

                    cap = ModelCapabilityInfo(
                        model_id=name,
                        provider="ollama",
                        display_name=name,
                        is_local=True,
                        is_free=True,
                        context_length=int(details.get("context_length") or 8192),
                        supports_tools="tools" in caps or bool(_CODING_PATTERNS.search(name)),
                        supports_structured_output=True,
                        supports_reasoning=bool(_REASONING_PATTERNS.search(name)),
                        supports_coding=bool(_CODING_PATTERNS.search(name)) or "coder" in name,
                        supports_vision="vision" in caps or bool(_VISION_PATTERNS.search(name)),
                        supports_streaming=True,
                        health_status="healthy",
                        latency_ms=latency_ms,
                        vram_mb_required=self._estimate_vram_mb(param_size),
                        parameter_size_b=param_size,
                        quantization=str(details.get("quantization_level", "Q4")),
                    )
                    models.append(cap)
        except Exception as exc:
            self._provider_health["ollama"] = {
                "provider": "ollama",
                "type": "local",
                "status": "offline",
                "model_count": 0,
                "latency_ms": 0.0,
                "last_checked": time.time(),
                "error": str(exc),
            }
        return models

    def _probe_lmstudio_direct(self) -> List[ModelCapabilityInfo]:
        models: List[ModelCapabilityInfo] = []
        host = os.environ.get("LMSTUDIO_HOST", "http://127.0.0.1:1234").rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}"

        t0 = time.monotonic()
        try:
            req = urllib.request.Request(f"{host}/v1/models", headers={"User-Agent": "OpenJarvis"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                raw_models = data.get("data", [])
                self._provider_health["lmstudio"] = {
                    "provider": "lmstudio",
                    "type": "local",
                    "status": "healthy",
                    "model_count": len(raw_models),
                    "latency_ms": latency_ms,
                    "last_checked": time.time(),
                    "error": None,
                }
                for m in raw_models:
                    m_id = m.get("id", "")
                    cap = self._analyze_model_capability(m_id, "lmstudio", is_local=True, latency_ms=latency_ms)
                    models.append(cap)
        except Exception:
            # LM Studio not running on 1234; silently record offline
            self._provider_health["lmstudio"] = {
                "provider": "lmstudio",
                "type": "local",
                "status": "offline",
                "model_count": 0,
                "latency_ms": 0.0,
                "last_checked": time.time(),
                "error": "Endpoint unreachable",
            }
        return models

    # --- Cloud Provider Probes ---

    def _probe_cloud_engines(self) -> List[ModelCapabilityInfo]:
        models: List[ModelCapabilityInfo] = []
        try:
            from openjarvis.engine.cloud import CloudEngine

            engine = CloudEngine()
            cloud_model_ids = engine.list_models()

            # Record health per active client
            cloud_clients = {
                "openai": engine._openai_client is not None,
                "anthropic": engine._anthropic_client is not None,
                "google": engine._google_client is not None,
                "deepseek": engine._deepseek_client is not None,
                "openrouter": engine._openrouter_client is not None,
            }

            for prov, connected in cloud_clients.items():
                self._provider_health[prov] = {
                    "provider": prov,
                    "type": "cloud",
                    "status": "healthy" if connected else "unconfigured",
                    "model_count": len([m for m in cloud_model_ids if prov in m.lower() or m.startswith(prov)]) if connected else 0,
                    "latency_ms": 150.0 if connected else 0.0,
                    "last_checked": time.time(),
                    "error": None if connected else "API key not configured",
                }

            for m_id in cloud_model_ids:
                prov = "cloud"
                if "gpt" in m_id.lower() or "o1" in m_id.lower() or "o3" in m_id.lower():
                    prov = "openai"
                elif "claude" in m_id.lower():
                    prov = "anthropic"
                elif "gemini" in m_id.lower():
                    prov = "google"
                elif "deepseek" in m_id.lower():
                    prov = "deepseek"
                elif m_id.startswith("openrouter/"):
                    prov = "openrouter"

                cap = self._analyze_model_capability(m_id, prov, is_local=False, latency_ms=180.0)
                models.append(cap)
        except Exception as exc:
            logger.debug("Cloud engine probe error: %s", exc)

        return models

    # --- Capability Heuristic & Metadata Analysis ---

    def _analyze_model_capability(
        self,
        model_id: str,
        provider: str,
        is_local: bool,
        latency_ms: float = 0.0,
    ) -> ModelCapabilityInfo:
        param_size = self._parse_param_size(model_id)
        is_coding = bool(_CODING_PATTERNS.search(model_id)) or "coder" in model_id.lower()
        is_reasoning = bool(_REASONING_PATTERNS.search(model_id)) or "r1" in model_id.lower()
        is_vision = bool(_VISION_PATTERNS.search(model_id))

        # Flag tool calling support (modern instruct / coder models support tool calling)
        supports_tools = True
        if "instruct" not in model_id.lower() and "chat" not in model_id.lower() and "coder" not in model_id.lower() and is_local and param_size < 3:
            supports_tools = False

        # Context length heuristics
        ctx = 8192
        if "128k" in model_id.lower() or "claude" in model_id.lower() or "gpt-4o" in model_id.lower():
            ctx = 128000
        elif "32k" in model_id.lower() or "qwen" in model_id.lower():
            ctx = 32768
        elif "16k" in model_id.lower():
            ctx = 16384

        return ModelCapabilityInfo(
            model_id=model_id,
            provider=provider,
            display_name=model_id.split("/")[-1],
            is_local=is_local,
            is_free=is_local,
            context_length=ctx,
            supports_tools=supports_tools,
            supports_structured_output=True,
            supports_reasoning=is_reasoning or (param_size >= 7.0 and "instruct" in model_id.lower()),
            supports_coding=is_coding or "claude-3-5" in model_id.lower() or "gpt-4" in model_id.lower(),
            supports_vision=is_vision,
            supports_streaming=True,
            health_status="healthy",
            latency_ms=latency_ms,
            vram_mb_required=self._estimate_vram_mb(param_size) if is_local else 0,
            parameter_size_b=param_size,
            privacy_classification="local" if is_local else "cloud-enterprise",
        )

    def _parse_param_size(self, text: str) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)\s*[Bb]", text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return 3.0  # Safe default estimate for compact models

    def _estimate_vram_mb(self, param_size_b: float) -> int:
        # Q4 4-bit quantization rule of thumb: ~650 MB per billion params + 500 MB KV cache
        if param_size_b <= 0:
            return 2048
        return int(param_size_b * 700 + 512)


def get_model_discovery_engine(config: Optional[JarvisConfig] = None) -> ModelDiscoveryEngine:
    return ModelDiscoveryEngine.get_instance(config=config)
