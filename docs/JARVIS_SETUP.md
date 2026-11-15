# OpenJarvis Setup & Operational Guide (Mark-LVII)

## 1. System Requirements & Hardware Specification

OpenJarvis Mark-LVII is specifically optimized for personal developer laptops with moderate VRAM:

- **CPU:** AMD Ryzen 5 5600H (6 Cores / 12 Threads) or equivalent.
- **RAM:** 16 GB DDR4/DDR5 system memory.
- **GPU:** NVIDIA GeForce GTX 1650 (4 GB GDDR6 VRAM) or compatible CUDA GPU.
- **OS:** Windows 11 (64-bit) or Linux (Ubuntu 22.04+).
- **Disk:** 10 GB free space for model weights, speech models, and SQLite journals.

---

## 2. Prerequisites

Ensure the following tools are installed:
- **Python:** 3.12 (via `pyenv`, `uv`, or python.org)
- **Node.js:** 22.x or later (with `npm` 11+)
- **NVIDIA Drivers:** Latest Game Ready or Studio Driver with CUDA 12.x support
- **Audio:** Working microphone and speaker/headphones (Bluetooth or built-in)

---

## 3. Installation & Environment Setup

### 3.1 Clone & Setup Python Virtual Environment

```powershell
# Navigate to workspace
cd d:\projects\oprn-jarvis

# Create virtual environment with Python 3.12
uv venv .venv --python 3.12

# Activate virtual environment
.venv\Scripts\Activate.ps1

# Install OpenJarvis in editable mode with development dependencies
uv pip install -e ".[dev]"
```

### 3.2 Speech & AI Model Dependencies

Verify that speech models and ONNX runtimes are present:
```powershell
uv pip install faster-whisper kokoro-onnx openwakeword sounddevice soundfile
```

### 3.3 Frontend Command Center Setup

```powershell
cd frontend
npm install
npm run build
cd ..
```

---

## 4. Configuration (`~/.openjarvis/config.toml`)

Create or edit `~/.openjarvis/config.toml`:

```toml
[server]
host = "127.0.0.1"
port = 8000
reload = false

[agent]
default_model = "qwen2.5:3b"
temperature = 0.7
max_tokens = 2048
context_from_memory = true

[memory]
enabled = true
backend = "local"
facts_path = "~/.openjarvis/facts.jsonl"
max_facts = 1000

[speech]
stt_engine = "faster-whisper"
stt_model = "base.en"
stt_device = "cpu"            # Conserves 4GB GPU VRAM for LLM inference
stt_compute_type = "int8"
tts_engine = "kokoro"
tts_voice = "af_heart"
tts_speed = 1.0
wake_word = "hey jarvis"
wake_word_model = "hey_jarvis"
echo_guard = true
guard_window_ms = 500

[routines]
timezone = "Asia/Kolkata"
quiet_hours_start = "22:00"
quiet_hours_end = "07:00"
suppress_voice_during_quiet = true
```

---

## 5. Running OpenJarvis

### 5.1 Start the API Server & Background Engine

```powershell
.venv\Scripts\python.exe -m openjarvis.server.app
```
Or using the CLI:
```powershell
jarvis serve --host 127.0.0.1 --port 8000
```

### 5.2 Start the React Command Center UI

In a separate terminal:
```powershell
cd frontend
npm run dev
```
Open `http://localhost:5173` in your browser.

### 5.3 Activate Continuous Voice Listening

To start voice listening with "Hey Jarvis" wake-word:
```powershell
jarvis listen
```
Or start wake-word detection via API:
```powershell
curl -X POST http://127.0.0.1:8000/v1/speech/wake/start
```

---

## 6. Verification & Test Suite Execution

Run all test suites to confirm full operational readiness:

```powershell
# Run core milestone test suites
.venv\Scripts\pytest.exe tests/speech/test_voice_advanced.py tests/tasks/test_tasks_engine.py tests/routines/test_routines.py tests/research/test_research_pipeline.py tests/memory/test_categorized_memory.py tests/security/test_action_journal.py tests/security/test_capabilities_router.py -v

# Run frontend test suite
npm --prefix frontend test
```
