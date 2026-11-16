"""
Phase 2 Live Model Benchmark and Verification Suite.
Evaluates local Ollama models, NIM endpoints, selector routing, fallback, and privacy isolation.
"""
import json
import os
import time
import urllib.request
from typing import Any, Dict

from openjarvis.models.discovery import ModelDiscoveryEngine
from openjarvis.models.selector import (
    ModelPolicy,
    ModelSelectionEngine,
    TaskType,
)


def test_ollama_live(model_name: str = "qwen3:4b-instruct") -> Dict[str, Any]:
    url = "http://127.0.0.1:11434/api/generate"
    # Basic math prompt
    t0 = time.time()
    payload = {
        "model": model_name,
        "prompt": "What is 15 + 27? Answer with only the number.",
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    t1 = time.time()
    response_text = data.get("response", "").strip()
    latency_ms = (t1 - t0) * 1000
    eval_count = data.get("eval_count", 0)
    eval_duration_ms = data.get("eval_duration", 0) / 1e6

    # Test structured JSON capability
    t2 = time.time()
    json_payload = {
        "model": model_name,
        "prompt": "Output a JSON object with keys 'status' (set to 'success') and 'code' (set to 200).",
        "stream": False,
        "format": "json",
    }
    req_json = urllib.request.Request(
        url,
        data=json.dumps(json_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_json, timeout=30) as resp:
        json_data = json.loads(resp.read().decode())
    t3 = time.time()
    json_resp_text = json_data.get("response", "").strip()
    json_parsed = None
    try:
        json_parsed = json.loads(json_resp_text)
    except Exception:
        pass

    return {
        "provider": "ollama",
        "model": model_name,
        "basic_prompt": "15 + 27",
        "basic_response": response_text,
        "latency_ms": round(latency_ms, 2),
        "eval_tokens": eval_count,
        "eval_duration_ms": round(eval_duration_ms, 2),
        "tokens_per_sec": (
            round((eval_count / (eval_duration_ms / 1000.0)), 2)
            if eval_duration_ms > 0
            else 0
        ),
        "structured_json_latency_ms": round((t3 - t2) * 1000, 2),
        "structured_json_valid": json_parsed is not None
        and json_parsed.get("status") == "success",
        "structured_json_response": json_resp_text,
    }


def run_benchmark():
    results = {}

    print("=== 1. Live Local Ollama Benchmark ===")
    try:
        ollama_res = test_ollama_live("qwen3:4b-instruct")
        results["ollama_qwen3_4b_instruct"] = ollama_res
        print(f"Model: {ollama_res['model']}")
        print(f"Basic Math Answer: {ollama_res['basic_response']}")
        print(f"Latency: {ollama_res['latency_ms']}ms")
        print(f"Eval Speed: {ollama_res['tokens_per_sec']} tokens/s")
        print(
            f"Structured JSON Success: {ollama_res['structured_json_valid']} ({ollama_res['structured_json_response']})"
        )
    except Exception as e:
        print(f"Ollama benchmark failed: {e}")
        results["ollama_error"] = str(e)

    print("\n=== 2. Model Discovery Audit ===")
    discovery = ModelDiscoveryEngine()
    models = discovery.discover_all()
    free_models = [m for m in discovery.get_all_models() if m.is_free]
    local_models = [m for m in discovery.get_all_models() if m.is_local]
    print(f"Discovered Total Models: {len(models)}")
    print(f"Free Models: {len(free_models)}")
    print(f"Local Models: {len(local_models)}")
    results["discovery"] = {
        "total_models": len(models),
        "free_models": len(free_models),
        "local_models": len(local_models),
        "providers_registered": [p["provider"] for p in discovery.list_providers()],
    }

    print("\n=== 3. Intelligent Model Selector Validation ===")
    selector = ModelSelectionEngine()

    # Task A: Coding Task (free-first default)
    selected_code = selector.select_model(
        task_type=TaskType.CODING, requires_tools=True
    )
    print(
        f"Coding Task -> Provider: {selected_code.provider}, Model: {selected_code.model_id}, Local: {selected_code.is_local}"
    )

    # Task B: Fast Chat Task
    selected_fast = selector.select_model(task_type=TaskType.CHAT)
    print(
        f"Fast Chat Task -> Provider: {selected_fast.provider}, Model: {selected_fast.model_id}, Local: {selected_fast.is_local}"
    )

    # Task C: Strict Privacy-First / Local-Only
    selected_local = selector.select_model(
        task_type=TaskType.SECURITY,
        local_only=True,
    )
    print(
        f"Privacy-First (Local Only) -> Provider: {selected_local.provider}, Model: {selected_local.model_id}, Local: {selected_local.is_local}"
    )

    results["selection"] = {
        "coding_task": {
            "model_id": selected_code.model_id,
            "provider": selected_code.provider,
            "is_local": selected_code.is_local,
            "score": selected_code.score,
        },
        "fast_chat": {
            "model_id": selected_fast.model_id,
            "provider": selected_fast.provider,
            "is_local": selected_fast.is_local,
            "score": selected_fast.score,
        },
        "privacy_first": {
            "model_id": selected_local.model_id,
            "provider": selected_local.provider,
            "is_local": selected_local.is_local,
            "score": selected_local.score,
        },
    }

    print("\n=== 4. Failover and Secret Redaction Validation ===")
    # Simulate failure on selected_code
    failover_candidate = selector.handle_model_failure(
        failed_model_id=selected_code.model_id,
        error="HTTP 429 Too Many Requests: key=sk-live-secret-test-key-12345 Bearer super-secret-token",
        task_type="code",
    )
    print(
        f"Failover triggered from {selected_code.model_id} -> {failover_candidate.model_id}"
    )
    history = [
        rec
        for rec in selector._failure_history
        if rec.get("model_id") == selected_code.model_id
    ]
    has_secret_leaked = any(
        "sk-live-secret-test-key-12345" in rec.get("error", "")
        or "super-secret-token" in rec.get("error", "")
        for rec in history
    )
    last_err = history[-1]["error"] if history else "No history"
    print(f"Logged Error in History: {last_err}")
    print(f"Secret Scrubbed Properly: {not has_secret_leaked}")

    results["failover"] = {
        "original_model": selected_code.model_id,
        "failover_model": failover_candidate.model_id,
        "secret_scrubbed": not has_secret_leaked,
    }

    # Save benchmark results
    out_path = os.path.join(os.path.dirname(__file__), "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nBenchmark results saved to: {out_path}")


if __name__ == "__main__":
    run_benchmark()
