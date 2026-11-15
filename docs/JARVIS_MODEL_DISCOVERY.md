# Jarvis Dynamic Model Discovery Architecture

## 1. Overview & Mission

OpenJarvis V1 previously relied on static, hardcoded model strings (`"qwen2.5:3b"`, `"gpt-4o-mini"`) across router fallbacks, workforce manifests, and heartbeat loops. The **Model Discovery Engine** (`src/openjarvis/models/discovery.py`) replaces all static declarations with dynamic, real-time discovery across local inference backends and cloud providers.

No model names are hardcoded into agent identities. Agent identities represent roles, tools, and capability requirements, while models are discovered, scored, and allocated dynamically per task.

---

## 2. Discovered Providers & Backends

The discovery engine queries two categories of inference endpoints:

### 2.1 Local Inference Services
| Provider | Default Endpoint | Detection Mechanism | Capabilities Detected |
| :--- | :--- | :--- | :--- |
| **vLLM** | `http://127.0.0.1:8000/v1` | OpenAI-compatible `/models` | Parameter size, context length, tool calling |
| **Ollama** | `http://127.0.0.1:11434` | Native `/api/tags` + OpenAI `/v1` | Quantization, parameter size, vision, thinking |
| **LM Studio** | `http://127.0.0.1:1234/v1` | OpenAI-compatible `/models` | Local quantized GGUF architectures |
| **NVIDIA NIM** | `http://localhost:8000/v1` | Container health + OpenAI `/models` | Optimized TensorRT-LLM containerized models |

### 2.2 Cloud Providers
| Provider | Supported Models | Credential Check |
| :--- | :--- | :--- |
| **Anthropic** | Claude 3.5 Sonnet, Claude 3.5 Haiku, Claude 3 Opus | `ANTHROPIC_API_KEY` |
| **OpenAI** | GPT-4o, GPT-4o-mini, o1, o3-mini | `OPENAI_API_KEY` |
| **Google** | Gemini 2.0 Flash, Gemini 1.5 Pro | `GEMINI_API_KEY` |
| **DeepSeek** | DeepSeek-V3, DeepSeek-R1 | `DEEPSEEK_API_KEY` |
| **OpenRouter** | 200+ models via live OpenRouter API | `OPENROUTER_API_KEY` |

---

## 3. Capability Heuristic & Verification Matrix

For every discovered model, the discovery engine constructs a `ModelCapabilityInfo` record with:
- `supports_tools`: Verified if instruct/chat/coder model with parameter size $\ge 3\text{B}$.
- `supports_structured_output`: Supported across all modern chat models.
- `supports_coding`: Triggered for models matching coder patterns or frontier models (e.g. `qwen2.5-coder`, `claude-3-5-sonnet`, `yi-large`).
- `supports_reasoning`: Detected for thinking/reasoning architectures (e.g. `deepseek-r1`, `o1`, `o3`).
- `supports_vision`: Detected for vision-language models (e.g. `qwen2.5vl`, `gpt-4o`).
- `vram_mb_required`: Calculated using the 4-bit quantization formula:
  $$\text{VRAM}_{\text{req}} = \text{ParamSize}_{\text{B}} \times 700\text{ MB} + 512\text{ MB}$$

---

## 4. REST API Endpoints

- `GET /v1/models/discovered`: Returns total count, local/cloud distribution, and complete capability metadata for all discovered models.
  - Parameter: `refresh=true` forces re-probing all endpoints.
- `GET /v1/models/providers`: Returns live health, connectivity status, latency in milliseconds, and model count per provider.

---

## 5. Security & Isolation

Model discovery runs non-destructively:
- Timeouts are bounded (maximum 3.0 seconds per endpoint probe).
- Offline endpoints are gracefully logged as `offline` without crashing the application.
- API keys are never exposed in log outputs or API responses.
