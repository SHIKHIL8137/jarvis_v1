# Jarvis V1 — Model Intelligence, Discovery & Allocation Source Audit

**Date:** 2026-11-15  
**Auditor:** Principal AI Systems & Model Routing Architect  
**Scope:** `src/openjarvis/workforce/`, `src/openjarvis/engine/`, `src/openjarvis/routing/`

---

## 1. Executive Summary

This audit assesses the state of model intelligence, discovery, and allocation across Jarvis V1. While Jarvis possesses foundational discovery primitives in `engine/_discovery.py` and dynamic auto-routing in `routing/auto_router.py`, the multi-agent workforce and engine layers suffer from **pervasive hardcoding of model identifiers**, static fallback lists, and decoupled model selection. 

Specialist agents (ATLAS, ORACLE, FORGE, etc.) were created with static strings like `"qwen2.5:3b"` and `"gpt-4o-mini"`. If those models are not installed locally or authenticated in the cloud, execution either silently fails, degrades, or falls back to an arbitrary hardcoded default.

---

## 2. Pervasive Hardcoded Model Identifiers (Source Findings)

### Issue MI-1: Static Model Preferences in Agent Manifests
- **Source File:** `src/openjarvis/workforce/definitions.py` (Lines 31-32, 53-54, 77-78, 100, 122, 144, 166)
- **Root Cause:** Every specialist agent definition explicitly hardcodes `model_preference="qwen2.5:3b"` and `model_fallback="gpt-4o-mini"` or `claude-3-5-sonnet-20241022`.
- **Integration Impact:** The agent identity is coupled to a specific model identifier. If `"qwen2.5:3b"` is missing, agents initialize in a degraded state or fail to execute tasks.
- **Proposed Fix:** Decouple agent identity from model identity. Replace `model_preference` with `required_capabilities` (`["planning", "reasoning"]`, `["code:execute", "file:read"]`, etc.) and context requirements. Make `model_preference` an optional user override.

### Issue MI-2: Hardcoded Model Fields in Workforce Models
- **Source File:** `src/openjarvis/workforce/models.py` (Lines 36-37)
- **Root Cause:** `AgentManifest` dataclass sets default field values `model_preference: str = "qwen2.5:3b"` and `model_fallback: str = "gpt-4o-mini"`.
- **Integration Impact:** Any instantiated manifest without an explicit model carries these hardcoded assumptions into `workforce.db`.
- **Proposed Fix:** Change defaults to `model_preference: Optional[str] = None` and `model_fallback: Optional[str] = None`. Add `required_capabilities: List[str]` and `hardware_constraints: Dict[str, Any]`.

### Issue MI-3: Hardcoded Model in Orchestrator Planning Prompt
- **Source File:** `src/openjarvis/workforce/orchestrator.py` (Line 87)
- **Root Cause:** When ATLAS invokes the inference engine to plan tasks, it defaults to `model=model_name or "qwen2.5:3b"`.
- **Integration Impact:** If `qwen2.5:3b` is not pulled or loaded in Ollama/vLLM, the LLM planning call throws an exception and falls back to heuristics.
- **Proposed Fix:** Query the centralized Model Selection Engine for the best available model with `structured_output` and `reasoning` capabilities.

### Issue MI-4: Static Fallback Model List in ModelRouter
- **Source File:** `src/openjarvis/engine/router.py` (Lines 27, 89-96)
- **Root Cause:** `default_model: str = "qwen2.5:3b"` and `list_models()` falls back to a hardcoded array:
  ```python
  available = [
      "qwen2.5:3b", "llama3.2:3b", "deepseek-r1:1.5b",
      "phi3:mini", "gpt-4o-mini", "claude-3-5-sonnet-20241022"
  ]
  ```
- **Integration Impact:** The system presents models as available when they may not exist on the host or in configured cloud accounts.
- **Proposed Fix:** Remove static array fallbacks. Return only discovered, verified, healthy models from reachable providers.

### Issue MI-5: Incomplete Discovery in AutoRouter
- **Source File:** `src/openjarvis/routing/auto_router.py` (Lines 80-147)
- **Root Cause:** `discover()` only calls `_ollama_models()` and `_openrouter_models()`. It completely ignores LM Studio, llama.cpp, vLLM, SGLang, and authenticated cloud providers in `openjarvis/engine/cloud.py` (OpenAI, Anthropic, Gemini, Groq, DeepSeek).
- **Integration Impact:** Users with LM Studio or direct OpenAI/Anthropic/Gemini keys cannot have their models auto-discovered or routed.
- **Proposed Fix:** Unify `auto_router.py` with `engine/_discovery.py` and `engine/cloud.py` into a unified Model Discovery Engine.

---

## 3. Required Model Capabilities Matrix

To eliminate hardcoded models, task allocation must query by standardized capability flags:

| Capability Flag | Description | Verified Via |
| :--- | :--- | :--- |
| `tool_calling` | Supports OpenAI function calling or JSON tool execution | Model metadata / tags |
| `structured_output` | Supports JSON schema output mode | Model parameters (`json_object`) |
| `reasoning` | High-depth chain-of-thought capability | Parameter size $\ge 7$B or model tags |
| `coding` | Syntactic precision, code generation, refactoring | Coding benchmark metadata |
| `vision` | Multimodal image input processing | Input modalities (`image`) |
| `long_context` | Context window $\ge 32$k tokens | `context_length` attribute |
| `local` | Runs on host (zero cloud data transmission) | Provider classification |

---

## 4. Hardware Awareness (Ryzen 5 5600H, GTX 1650 4 GB VRAM)

- **Problem:** If multiple agents execute simultaneously, loading multiple models into the 4 GB VRAM envelope triggers CUDA Out of Memory (OOM) errors.
- **Architecture Requirement:**
  1. A shared local model pool (reuse currently loaded model in Ollama/vLLM/LM Studio).
  2. Bounded local concurrency (`max_concurrent_local = 2`).
  3. Strict VRAM reservation check before loading any model $> 4$B parameters without Q4/Q5 quantization.
  4. Explicit fallback to cloud only if user policy allows (`cloud_fallback_enabled = True`).
