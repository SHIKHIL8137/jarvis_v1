# Jarvis

A personal AI assistant that runs on your own PC. It talks and listens, works on your code projects
(plan, build, test, review), opens and operates apps, browses the web, and picks the right model for each
job. Local models are the default; cloud models are optional. Anything sensitive asks you first.

> **Status:** a personal project under active development. It works end to end on Windows 11 with Ollama;
> expect rough edges. Based on [OpenJarvis](https://github.com/open-jarvis/OpenJarvis), see [License](#license-and-credits).

## What it can do

| | |
|---|---|
| **Voice conversation** | Hands-free mode: local Whisper speech-to-text, spoken replies, and you can interrupt by speaking. |
| **Coding agent** | `/goal <task>` plans, edits, runs the tests and only stops when a reviewer confirms with evidence. Also `/plan`, `/review`, `/init`, and plain "implement X in D:\projects\app". |
| **Specialist subagents** | Read-only explorer, planner and reviewer agents; several can run in parallel. |
| **Automatic model choice** | The **Auto** model discovers your local and OpenRouter models, probes tool-calling, and learns from failures and speed. No model names are hard-coded. |
| **Desktop and browser** | Opens apps and URLs, reads the screen with OCR, clicks and types (off until you enable it), drives a Playwright browser. |
| **Folder permissions** | Grant a project folder once; routine commands stop asking, risky ones (delete, install, push) always do. |
| **Web search** | Optional Tavily key for live web search. |

## Quick start (Windows)

You need [uv](https://docs.astral.sh/uv/), Node.js 22.22+ with npm 11.19+, and [Ollama](https://ollama.com/)
with a model that supports tools, for example `ollama pull qwen3:4b-instruct`.

```powershell
git clone https://github.com/SHIKHIL8137/open-jarvis.git
cd open-jarvis
uv sync --extra server --extra computer-use
uv run playwright install chromium        # only for the browser tools
powershell -File scripts\start-jarvis.ps1
```

Then open <http://127.0.0.1:5173>.

`scripts\start-jarvis.ps1` sets `OLLAMA_HOST` to a valid URL (a bare `127.0.0.1:11434` is rejected) and loads any
cloud keys from `~\.openjarvis\cloud-keys.env` before starting.

### Cloud models (optional)

Put keys in `~\.openjarvis\cloud-keys.env`, one per line (for example `OPENROUTER_API_KEY=...`), then restart. The browser
Settings page cannot save keys; it only shows which ones the server has. For web search, add
`TAVILY_API_KEY` and install the extra: `uv sync --extra tools-search`.

## Using the coding agent

Type these in the chat (or say them in voice mode):

| Message | Result |
|---|---|
| `/goal Fix the failing tests in D:\projects\app` | Works in rounds until a reviewer, judging only evidence, says done, blocked or stuck. |
| `/plan Add rate limiting to D:\projects\app` | Read-only exploration and a written plan. Nothing changes. |
| `/review` | Reviews uncommitted changes (or a commit, branch or PR). |
| `/init` | Creates or improves the project's `AGENTS.md`. |

Details are in [docs/coding-agent.md](docs/coding-agent.md).

## Configuration

Everything lives in `~\.openjarvis\config.toml`:

```toml
[tools]
enabled = ["file_read", "edit", "grep", "glob", "shell_exec", "subagent", "todo_write", "open_app", "screen_ocr"]

[security]
allowed_dirs = ["D:\\projects"]      # folders the file tools may touch

[goal]
max_rounds = 12                      # most work/review rounds per goal
subagent_depth = 1
auto = true                          # a clear "implement X in <project>" request starts a goal

[routing]                            # optional
max_price_per_m = 3.0                # max USD per million output tokens for Auto
# code = "some/model"                # pin a task type to one model
```

## Safety model

- Commands that only read, test, lint or build run on their own. Anything else, and anything risky, waits in the
  **approvals bell** for your answer. "Approve & always allow" remembers a folder (revoke it in Settings).
- Desktop control (clicking, typing, key presses) is **off** by default; switch it on in Settings.
- Typing into login, password and payment windows is always refused.
- Agents are limited to the folders in `allowed_dirs`. Read-only agents have no write or shell tools at all.
- Approvals are protected by being a local server, not by a hard boundary: a model that runs its own code could
  call the local API. Keep `shell_exec` approvals on.

## Development

```powershell
uv sync --extra server --extra dev
uv run pytest tests/coding tests/tools tests/test_auto_router.py   # the parts added in this fork
cd frontend; npm install; npx tsc --noEmit
```

The Python package is still named `openjarvis` (`src/openjarvis`), and the CLI is `jarvis`.

### Troubleshooting

- **Memory shows "backend unavailable":** build the Rust extension: `uv run maturin develop --release -m rust/crates/openjarvis-python/Cargo.toml` (needs Rust 1.88+ and the MSVC C++ build tools).
  If the module still imports empty, add `from .openjarvis_rust import *` to `.venv\Lib\site-packages\openjarvis_rust\__init__.py`.
- **Speech fails with `cublas64_12.dll`:** set `[speech] device = "cpu"` and `compute_type = "int8"`.
- **"No inference engine available":** make sure `OLLAMA_HOST` starts with `http://`.
- Do not run a plain `uv sync` afterwards if you added packages with `uv pip`; use `uv sync --inexact`.

## License and credits

Licensed under the **Apache License 2.0** (see [LICENSE](LICENSE)). Jarvis is a derivative of
[OpenJarvis](https://github.com/open-jarvis/OpenJarvis) by the OpenJarvis Contributors (Apache-2.0); the
required notices are kept in [NOTICE](NOTICE). Changes in this fork are Copyright 2026 SHIKHIL8137, also under Apache-2.0.
