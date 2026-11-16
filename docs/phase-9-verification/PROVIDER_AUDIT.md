# JARVIS V1 — PHASE 9 AI MODEL & PROVIDER AUDIT

**Date:** 2026-11-16  
**Auditor:** Principal AI/ML Engineer  
**Target:** OpenJarvis Model Discovery, Selection, and Provider Routing  
**Status:** VERIFIED & COMPLIANT

---

## 1. Executive Summary

An audit of the OpenJarvis model infrastructure was executed against the **Free-First AI Model Policy**. The system guarantees zero mandatory paid API dependencies, executes local inferences via Ollama on consumer-tier hardware, discovers verified zero-cost cloud endpoints, and hard-blocks paid providers by default.

---

## 2. Free-First Provider Architecture

```
                      +-----------------------------+
                      | User Request / Task Prompt  |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |    ModelSelectionEngine     |
                      |  (Hardware & Policy Gates)  |
                      +--------------+--------------+
                                     |
           +-------------------------+-------------------------+
           |                                                   |
           v                                                   v
   [Local-First / Privacy]                             [Cloud-First / Overflow]
+----------------------+                             +--------------------------+
| Ollama Local Daemon  |                             | OpenRouter Free / NIM    |
| (127.0.0.1:11434)    |                             | (prompt=0, compl=0)      |
| Models:              |                             | 17 Verified Free Models  |
| - qwen3:4b-instruct  |                             +-------------+------------+
| - qwen2.5vl:3b       |                                           |
+----------------------+                                           v
                                                     +--------------------------+
                                                     | Paid Providers (OpenAI,  |
                                                     | Anthropic, etc.)         |
                                                     | BLOCKED BY DEFAULT       |
                                                     | (Explicit allow_paid req)|
                                                     +--------------------------+
```

---

## 3. Local Inference Verification (Ollama)

### Environment Status
- **Host Endpoint:** `http://127.0.0.1:11434` (Healthy, response: `"Ollama is running"`)
- **GPU Accelerator:** NVIDIA GeForce GTX 1650 (4.0 GB VRAM detected)
- **Local Models Installed:**
  1. `qwen3:4b-instruct` (4.0B parameters, supports tools and thinking)
  2. `qwen2.5vl:3b` (3.0B parameters, vision + multimodal support)
  3. `qwen3:4b` (4.0B general base model)

### Live End-to-End Inference Evidence
Executed live test query to `http://127.0.0.1:11434/api/generate`:
```json
{
  "model": "qwen3:4b-instruct",
  "prompt": "Jarvis system status check: Respond in one sentence.",
  "stream": false
}
```
**Response Result:**
- **HTTP Status:** 200 OK
- **Generated Response:** `"I'm ready to assist you with any task or information you need."`
- **Output Token Count:** 15 tokens
- **Inference Latency:** 1149 ms (~13.1 tokens/sec on GTX 1650)
- **Verification:** PASS

---

## 4. OpenRouter Free Tier Discovery & Verification

### Status & Capabilities
- **Endpoint:** `https://openrouter.ai/api/v1/models`
- **Authentication:** Unauthenticated public discovery verified (DEF-02 remediated).
- **Free Model Filtering Rule:**
  - `pricing.prompt == "0"` AND `pricing.completion == "0"`
  - Suffix `:free` detection fallback.
- **Active Free Models Discovered:** 17 verified models including:
  - `meta-llama/llama-3.3-70b-instruct:free`
  - `deepseek/deepseek-r1:free`
  - `google/gemini-2.0-flash-exp:free`
  - `qwen/qwen-2.5-72b-instruct:free`
  - `mistralai/mistral-small-24b-instruct-2501:free`

---

## 5. Paid Model Protection & Remediation (DEF-01)

### Remediation Details
- **Location:** `src/openjarvis/models/selector.py`
- **Fix:** In `ModelSelectionEngine.select_model()`, the default value of `effective_allow_paid` is now strictly `self.allow_paid_models` (False). Setting `ModelPolicy.CLOUD_FIRST` routes only to free cloud models (OpenRouter `:free` or free tiers) and does NOT enable paid models unless the caller explicitly sets `allow_paid=True`.
- **Regression Test:** `tests/models/test_free_model_routing.py::test_scenario_10_paid_provider_cannot_be_used_unless_explicitly_enabled` (13/13 tests passed).

---

## 6. Hardware-Aware Resource Management

| Resource Metric | Host Machine | Allocation Policy | Engine Enforcement |
| :--- | :--- | :--- | :--- |
| **GPU VRAM** | 4.0 GB (GTX 1650) | 85% Budget = 3,481 MB | Local models > 3.4GB rejected |
| **System RAM** | 15.3 GB Available | Guarded at 80% ceiling | Concurrency capped at 2 local slots |
| **CPU Threads** | 12 Logical (AMD Ryzen 5) | Worker threads = 4 | Subprocesses assigned CPU affinity |
| **Model Weight Retention**| Single active local model | Cache loaded weights | +15 score bonus for loaded model |

---

## 7. Provider Verification Matrix

| Requirement | Specification | Result | Evidence |
| :--- | :--- | :--- | :--- |
| Zero Paid Dependencies | System operates with zero paid APIs | VERIFIED | 100% free model routing verified |
| Local Model Execution | Ollama runs locally | VERIFIED | `qwen3:4b-instruct` live HTTP 200 response |
| Public Discovery | OpenRouter models discovered without key | VERIFIED | DEF-02 fix; 17 free models parsed |
| Privacy-First Gate | Never falls back to cloud | VERIFIED | Strict local boundary throws on missing local model |
| Concurrency Throttling | Prevents OOM crashes | VERIFIED | VRAM limit MB budget enforced |
