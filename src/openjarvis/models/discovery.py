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
from openjarvis.models.capabilities import CapabilityProvenance, ModelCapabilityInfo

logger = logging.getLogger(__name__)

# Known coding model pattern keywords
_CODING_PATTERNS = re.compile(r"(coder|code|starcoder|codellama|codestral|granite.*code)", re.IGNORECASE)
# Known reasoning / thinking model pattern keywords
_REASONING_PATTERNS = re.compile(r"(reason|think|deepseek-r1|qwq|\bo1\b|\bo3\b|\br1\b)", re.IGNORECASE)
# Known vision / multimodal model pattern keywords
_VISION_PATTERNS = re.compile(r"(vision|vl|omni|\b4o\b|gemini|fuyu|kosmos|deplot|neva)", re.IGNORECASE)

# Models explicitly known NOT to support tool/function calling
KNOWN_NON_TOOL_MODELS: frozenset[str] = frozenset({
    "deepseek-reasoner",
    "deepseek-r1",
    "o1-preview",
    "o1-mini",
    "gpt-3.5-turbo-instruct",
    "text-davinci-003",
    "claude-2.0",
    "claude-2.1",
    "claude-instant-1.2",
    "embedding",
    "bge-",
    "rerank",
})

# Models explicitly confirmed to support function/tool calling
KNOWN_TOOL_MODELS: frozenset[str] = frozenset({
    "gpt-4",
    "gpt-5",
    "o3",
    "claude-3",
    "claude-sonnet",
    "claude-haiku",
    "claude-opus",
    "gemini-1.5",
    "gemini-2",
    "gemini-2.5",
    "gemini-3",
    "deepseek-chat",
    "qwen2.5-coder",
    "qwen3",
})


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

                    supp_tools = "tools" in caps or bool(_CODING_PATTERNS.search(name))
                    supp_coding = bool(_CODING_PATTERNS.search(name)) or "coder" in name
                    supp_reasoning = bool(_REASONING_PATTERNS.search(name))
                    supp_vision = "vision" in caps or bool(_VISION_PATTERNS.search(name))

                    ollama_provenance = {
                        "streaming": CapabilityProvenance.PROVIDER_DECLARED.value,
                        "structured_output": CapabilityProvenance.PROVIDER_DECLARED.value,
                        "tools": CapabilityProvenance.PROVIDER_DECLARED.value if "tools" in caps else (CapabilityProvenance.INFERRED.value if supp_tools else CapabilityProvenance.UNKNOWN.value),
                        "coding": CapabilityProvenance.INFERRED.value if supp_coding else CapabilityProvenance.UNKNOWN.value,
                        "reasoning": CapabilityProvenance.INFERRED.value if supp_reasoning else CapabilityProvenance.UNKNOWN.value,
                        "vision": CapabilityProvenance.PROVIDER_DECLARED.value if "vision" in caps else (CapabilityProvenance.INFERRED.value if supp_vision else CapabilityProvenance.UNKNOWN.value),
                    }

                    cap = ModelCapabilityInfo(
                        model_id=name,
                        provider="ollama",
                        display_name=name,
                        is_local=True,
                        is_free=True,
                        context_length=int(details.get("context_length") or 8192),
                        supports_tools=supp_tools,
                        supports_structured_output=True,
                        supports_reasoning=supp_reasoning,
                        supports_coding=supp_coding,
                        supports_vision=supp_vision,
                        supports_streaming=True,
                        health_status="healthy",
                        latency_ms=latency_ms,
                        vram_mb_required=self._estimate_vram_mb(param_size),
                        parameter_size_b=param_size,
                        quantization=str(details.get("quantization_level", "Q4")),
                        confirmed_capabilities=["streaming"],
                        provider_declared_capabilities=caps,
                        provenance=ollama_provenance,
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

            # Map of provider name -> (client, probe_function)
            providers_to_probe = [
                ("openai", engine._openai_client),
                ("anthropic", engine._anthropic_client),
                ("google", engine._google_client),
                ("deepseek", engine._deepseek_client),
                ("openrouter", engine._openrouter_client),
            ]

            for prov, client in providers_to_probe:
                # If neither client nor API key is configured
                env_key_map = {
                    "openai": "OPENAI_API_KEY",
                    "anthropic": "ANTHROPIC_API_KEY",
                    "google": "GEMINI_API_KEY",
                    "deepseek": "DEEPSEEK_API_KEY",
                    "openrouter": "OPENROUTER_API_KEY",
                }
                env_key = os.environ.get(env_key_map.get(prov, ""))

                if client is None and not env_key:
                    self._provider_health[prov] = {
                        "provider": prov,
                        "type": "cloud",
                        "status": "unconfigured",
                        "connected": False,
                        "model_count": 0,
                        "latency_ms": 0.0,
                        "last_checked": time.time(),
                        "error": "API key not configured",
                    }
                    continue

                # Live query provider model list API and measure real latency
                t0 = time.monotonic()
                prov_models: List[str] = []
                try:
                    if prov in ("openai", "deepseek", "openrouter") and hasattr(client, "models") and hasattr(client.models, "list"):
                        list_resp = client.models.list()
                        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                        data_items = getattr(list_resp, "data", [])
                        for item in data_items:
                            m_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else str(item))
                            if m_id:
                                prov_models.append(m_id)
                    elif prov == "google" and hasattr(client, "models") and hasattr(client.models, "list"):
                        list_resp = client.models.list()
                        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                        for m in list_resp:
                            m_name = getattr(m, "name", str(m))
                            prov_models.append(m_name)
                    elif prov == "anthropic":
                        if client is not None and hasattr(client, "models") and hasattr(client.models, "list"):
                            list_resp = client.models.list()
                            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                            data_items = getattr(list_resp, "data", [])
                            for item in data_items:
                                m_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else str(item))
                                if m_id:
                                    prov_models.append(m_id)
                        else:
                            api_key = env_key or getattr(client, "api_key", "")
                            req = urllib.request.Request(
                                "https://api.anthropic.com/v1/models",
                                headers={
                                    "x-api-key": api_key,
                                    "anthropic-version": "2023-06-01",
                                    "User-Agent": "OpenJarvis",
                                },
                                method="GET",
                            )
                            with urllib.request.urlopen(req, timeout=3.0) as resp:
                                elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                                data = json.loads(resp.read().decode("utf-8"))
                                for item in data.get("data", []):
                                    m_id = item.get("id")
                                    if m_id:
                                        prov_models.append(m_id)
                    else:
                        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                        prov_models = []

                    self._provider_health[prov] = {
                        "provider": prov,
                        "type": "cloud",
                        "status": "healthy" if prov_models else "degraded",
                        "connected": True,
                        "model_count": len(prov_models),
                        "latency_ms": elapsed_ms,
                        "last_checked": time.time(),
                        "error": None if prov_models else "No models returned from provider API",
                    }

                    for m_id in prov_models:
                        cap = self._analyze_model_capability(m_id, prov, is_local=False, latency_ms=elapsed_ms)
                        models.append(cap)

                except Exception as exc:
                    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
                    err_str = str(exc).lower()
                    connected = False
                    if "auth" in err_str or "401" in err_str or "unauthorized" in err_str or "invalid api key" in err_str:
                        status = "auth_error"
                    elif "429" in err_str or "rate limit" in err_str or "quota" in err_str:
                        status = "rate_limited"
                        connected = True
                    elif "404" in err_str or "405" in err_str or "not found" in err_str or "unsupported" in err_str:
                        status = "unsupported_endpoint"
                        connected = True
                    elif "timeout" in err_str or "connection" in err_str or "unreachable" in err_str:
                        status = "unreachable"
                    else:
                        status = "offline"

                    self._provider_health[prov] = {
                        "provider": prov,
                        "type": "cloud",
                        "status": status,
                        "connected": connected,
                        "model_count": 0,
                        "latency_ms": elapsed_ms,
                        "last_checked": time.time(),
                        "error": str(exc),
                    }
                    # Offline / unconfigured providers contribute zero selectable models
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
        m_lower = model_id.lower()
        param_size = self._parse_param_size(model_id)
        is_coding = bool(_CODING_PATTERNS.search(model_id)) or "coder" in m_lower or "claude-3-5" in m_lower or "gpt-4" in m_lower or "o3" in m_lower
        is_reasoning = bool(_REASONING_PATTERNS.search(model_id)) or "r1" in m_lower or "o1" in m_lower or "o3" in m_lower
        is_vision = bool(_VISION_PATTERNS.search(model_id))

        # Provenance attribution
        provenance: Dict[str, str] = {
            "streaming": CapabilityProvenance.PROVIDER_DECLARED.value,
            "structured_output": CapabilityProvenance.PROVIDER_DECLARED.value if not is_local else CapabilityProvenance.INFERRED.value,
        }
        confirmed: List[str] = ["streaming"]
        declared: List[str] = ["streaming"] if not is_local else []

        # Tool calling capability determination: DO NOT mark every cloud model as tool-capable
        supports_tools = False
        if any(non_tool in m_lower for non_tool in KNOWN_NON_TOOL_MODELS):
            supports_tools = False
            provenance["tools"] = CapabilityProvenance.UNKNOWN.value
        elif any(tool_kw in m_lower for tool_kw in KNOWN_TOOL_MODELS):
            supports_tools = True
            provenance["tools"] = CapabilityProvenance.PROVIDER_DECLARED.value
            declared.append("tools")
        elif is_local:
            if "instruct" in m_lower or "chat" in m_lower or "coder" in m_lower or param_size >= 7.0:
                supports_tools = True
                provenance["tools"] = CapabilityProvenance.INFERRED.value
            else:
                supports_tools = False
                provenance["tools"] = CapabilityProvenance.UNKNOWN.value
        else:
            # Cloud model not confirmed in tool allowlist: never assume tool capable!
            supports_tools = False
            provenance["tools"] = CapabilityProvenance.UNKNOWN.value

        # Coding provenance
        if is_coding:
            provenance["coding"] = CapabilityProvenance.PROVIDER_DECLARED.value if ("coder" in m_lower or "claude-3-5" in m_lower) else CapabilityProvenance.INFERRED.value
            if provenance["coding"] == CapabilityProvenance.PROVIDER_DECLARED.value:
                declared.append("coding")
        else:
            provenance["coding"] = CapabilityProvenance.UNKNOWN.value

        # Reasoning provenance
        if is_reasoning:
            provenance["reasoning"] = CapabilityProvenance.PROVIDER_DECLARED.value if ("deepseek-r1" in m_lower or "o1" in m_lower or "o3" in m_lower) else CapabilityProvenance.INFERRED.value
            if provenance["reasoning"] == CapabilityProvenance.PROVIDER_DECLARED.value:
                declared.append("reasoning")
        else:
            provenance["reasoning"] = CapabilityProvenance.UNKNOWN.value

        # Vision provenance
        if is_vision:
            provenance["vision"] = CapabilityProvenance.PROVIDER_DECLARED.value if ("omni" in m_lower or "4o" in m_lower or "gemini" in m_lower) else CapabilityProvenance.INFERRED.value
            if provenance["vision"] == CapabilityProvenance.PROVIDER_DECLARED.value:
                declared.append("vision")
        else:
            provenance["vision"] = CapabilityProvenance.UNKNOWN.value

        # Context length heuristics
        ctx = 8192
        if "128k" in m_lower or "claude" in m_lower or "gpt-4o" in m_lower:
            ctx = 128000
        elif "32k" in m_lower or "qwen" in m_lower:
            ctx = 32768
        elif "16k" in m_lower:
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
            supports_reasoning=is_reasoning or (param_size >= 7.0 and "instruct" in m_lower),
            supports_coding=is_coding,
            supports_vision=is_vision,
            supports_streaming=True,
            health_status="healthy",
            latency_ms=latency_ms,
            vram_mb_required=self._estimate_vram_mb(param_size) if is_local else 0,
            parameter_size_b=param_size,
            privacy_classification="local" if is_local else "cloud-enterprise",
            confirmed_capabilities=confirmed,
            provider_declared_capabilities=declared,
            provenance=provenance,
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
