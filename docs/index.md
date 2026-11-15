---
title: Jarvis
description: A personal AI assistant that runs on your own PC
search:
  boost: 2
hide:
  - navigation
---

# Jarvis: your personal AI assistant

<p class="hero-tagline">
A personal AI assistant that runs on your own PC: it talks and listens, works on your projects,
opens and operates apps, and picks the right model for each job. Cloud models are optional.
</p>


## Why Jarvis?

Most AI assistants send everything to someone else's server and only chat. Jarvis runs on your own machine,
uses local models by default and cloud models only when you allow it, and actually does the work: it reads and edits
your projects, runs and checks the tests, opens apps and browses the web, and asks you before anything sensitive.

See the [coding agent](coding-agent.md) for the plan, build, test and review workflow.

---

## Get Started

=== "Browser App"

    Run the full chat UI locally with one script:

    ```bash
    git clone https://github.com/SHIKHIL8137/open-jarvis.git
    cd OpenJarvis
    ./scripts/quickstart.sh
    ```

    This installs dependencies, starts Ollama + a local model, launches the backend
    and frontend, and opens `http://localhost:5173` in your browser.

=== "Desktop App"

    The desktop app is a native window for the OpenJarvis UI.
    The backend (Ollama + inference) runs on your machine — start it first, then open the app.

    **Step 1.** Start the backend:

    ```bash
    git clone https://github.com/SHIKHIL8137/open-jarvis.git
    cd OpenJarvis
    ./scripts/quickstart.sh
    ```

    **Step 2.** Download and open the desktop app:

    [Download for macOS](https://github.com/SHIKHIL8137/open-jarvis/releases/latest/download/OpenJarvis_universal.dmg){ .md-button .md-button--primary }

    Also available for [Windows](https://github.com/SHIKHIL8137/open-jarvis/releases/latest/download/OpenJarvis_x64-setup.exe), [Linux (DEB)](https://github.com/SHIKHIL8137/open-jarvis/releases/latest/download/OpenJarvis_amd64.deb), and [Linux (RPM)](https://github.com/SHIKHIL8137/open-jarvis/releases/latest/download/OpenJarvis_x86_64.rpm). See the [Downloads](downloads.md) page for details.

    The app connects to `http://localhost:8000` automatically.

    !!! warning "macOS first launch"

        Run `xattr -cr /Applications/OpenJarvis.app` if the app shows as "damaged".

=== "Python SDK"

    ```python
    from openjarvis import Jarvis

    j = Jarvis()                              # auto-detect engine
    response = j.ask("Explain quicksort.")
    print(response)
    ```

    For more control, use `ask_full()` to get usage stats, model info, and tool results:

    ```python
    result = j.ask_full(
        "What is 2 + 2?",
        agent="orchestrator",
        tools=["calculator"],
    )
    print(result["content"])       # "4"
    print(result["tool_results"])  # [{tool_name: "calculator", ...}]
    ```

=== "CLI"

    ```bash
    jarvis ask "What is the capital of France?"

    jarvis ask --agent orchestrator --tools calculator "What is 137 * 42?"

    jarvis serve --port 8000

    jarvis memory index ./docs/
    jarvis memory search "configuration options"
    ```

---

## Five Primitives for Personal AI

OpenJarvis is built around five composable layers. Each has a clean interface and can be swapped independently.

1. **Intelligence** — Pick a model, or let OpenJarvis pick one for your hardware. Manages the full catalog of local models across providers.
2. **Engine** — The inference runtime: [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), [SGLang](https://github.com/sgl-project/sglang), [llama.cpp](https://github.com/ggerganov/llama.cpp), cloud APIs, and more. Auto-detects your hardware and recommends the best fit.
3. **Agents** — Multi-step reasoning with tool use. Eight built-in agent types from simple chat to orchestrated workflows.
4. **Tools & Memory** — Web search, calculator, file I/O, code interpreter, retrieval, persistent local state, and any external MCP server.
5. **Learning** — Your AI gets better over time. Every interaction generates traces that drive automatic improvements to model weights, prompts, and agent behavior.

---

## Key Features

<div class="grid cards" markdown>

-   **10+ Engine Backends**

    ---

    [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), [SGLang](https://github.com/sgl-project/sglang), [llama.cpp](https://github.com/ggerganov/llama.cpp), [MLX](https://github.com/ml-explore/mlx), [Exo](https://github.com/exo-explore/exo), [LiteLLM](https://github.com/BerriAI/litellm), cloud (OpenAI/Anthropic/Google), and more. Same `InferenceEngine` interface, swap freely.

-   **Automated Workflows**

    ---

    Cron-based agents that monitor, summarize, and act. Code review, email triage, research digests — running 24/7 on your hardware.

-   **Hardware-Aware**

    ---

    Auto-detects GPU vendor, model, and VRAM. Recommends the optimal engine for your hardware.

-   **Offline-First**

    ---

    All core functionality works without a network connection. Cloud APIs are optional extras.

-   **OpenAI-Compatible API**

    ---

    `jarvis serve` starts a FastAPI server with SSE streaming. Drop-in replacement for OpenAI clients.

-   **Energy & Cost Tracking**

    ---

    Built-in telemetry for GPU power draw, token costs, and latency. See exactly what each query costs in watts and dollars.

</div>

---

## Documentation

<div class="grid cards" markdown>

-   **[Getting Started](getting-started/installation.md)**

    ---

    Install OpenJarvis, configure your first engine, and run your first query.

-   **[User Guide](user-guide/cli.md)**

    ---

    CLI, Python SDK, and guides for [Morning Digest](user-guide/morning-digest.md), [Deep Research](user-guide/deep-research.md), [Code Assistant](user-guide/code-assistant.md), [Scheduled Monitor](user-guide/scheduled-monitor.md), [Simple Chat](user-guide/chat-simple.md), [Evaluations](user-guide/evaluations.md), agents, memory, tools, and telemetry.

-   **[Architecture](architecture/overview.md)**

    ---

    Five-primitive design, registry pattern, query flow, and cross-cutting learning.

-   **[API Reference](api-reference/openjarvis/index.md)**

    ---

    Auto-generated reference for every module.

-   **[Deployment](deployment/docker.md)**

    ---

    Docker, systemd, launchd. GPU-accelerated container images.

-   **[Development](development/contributing.md)**

    ---

    Contributing guide, extension patterns, roadmap, and changelog.

</div>

## License and credits

Jarvis is licensed under Apache-2.0 and is based on [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) (Apache-2.0). See `LICENSE` and `NOTICE` in the repository.
