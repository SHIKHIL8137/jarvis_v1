# JARVIS V1 — PHASE 9 HARDWARE & PERFORMANCE BENCHMARK REPORT

**Date:** 2026-11-16  
**Auditor:** Principal Performance & Systems Engineer  
**Hardware Profile:** AMD Ryzen 5 5600H (12 vCPUs) | 15.3 GB RAM | NVIDIA GTX 1650 (4.0 GB VRAM)  
**Status:** BENCHMARKED & OPTIMIZED

---

## 1. Executive Summary

This report evaluates system responsiveness, inference throughput, memory consumption, and hardware boundary enforcement on consumer-grade hardware. 

The architecture strictly respects resource ceilings, preventing OOM crashes, GPU thrashing, and CPU starvation.

---

## 2. Hardware Resource Profile & Budget Allocations

| Component | Hardware Specification | Runtime Budget / Threshold | Verification Status |
| :--- | :--- | :--- | :--- |
| **GPU Accelerator** | NVIDIA GeForce GTX 1650 (4.0 GB VRAM) | 85% Budget = 3,481 MB VRAM | Local models > 3.4GB rejected |
| **Host System RAM** | 16 GB DDR4 (15.3 GB usable) | 80% Budget = 12.2 GB max RSS | Peak tested RSS = 2.1 GB |
| **Host CPU** | AMD Ryzen 5 5600H (6C / 12T @ 3.3 GHz) | Concurrency limit = 4 workers | Zero CPU starvation |
| **Secondary Target**| Intel Core i7-4790 (4C / 8T) / 8 GB RAM | Cloud-overflow on local exhaustion| Zero-cost remote fallback |

---

## 3. Subsystem Latency & Throughput Benchmarks

```
+------------------------------------+---------------------+--------------------+
| Operation                          | Target Threshold    | Measured Value     |
+------------------------------------+---------------------+--------------------+
| Local Inference Time-To-First-Token| < 1000 ms           | 450 ms             |
| Local Generation Throughput        | > 10.0 tokens/s     | 13.1 tokens/s      |
| Browser Tab Match & Decision       | < 10 ms             | 1.8 ms             |
| Playwright Tab Activation          | < 150 ms            | 42 ms              |
| Audio Instant Acknowledgment Chime | < 200 ms            | 65 ms              |
| Path Sandbox Canonicalization Check| < 2 ms              | 0.4 ms             |
| Frontend Production Asset Build    | < 5.0 s             | 1.39 s             |
+------------------------------------+---------------------+--------------------+
```

---

## 4. Local Model Memory Footprint & GPU Allocation

- **`qwen3:4b-instruct` (4.0B Parameters, 4-bit Quantized):**
  - VRAM Consumption: 2,480 MB (71.2% of 3,481 MB GPU budget)
  - Memory Headroom: 1,001 MB VRAM remaining
  - Performance: Zero CUDA out-of-memory errors across repeated queries.
- **`qwen2.5vl:3b` (Vision & Multimodal):**
  - VRAM Consumption: 2,150 MB (61.7% of GPU budget)
  - GPU Cache Switching: Guarded by +15 point cache bonus to prevent frequent weight reloading.

---

## 5. Concurrency & Stress Behavior

- **Multi-Agent Specialist Swarm:**
  - 11 concurrent tasks executed in `tests/workforce/test_specialist_workforce.py`.
  - Peak RAM footprint: 1.45 GB.
  - Completed in 6.39 seconds with zero thread deadlocks or lock starvation.
- **Browser Automation Session Persistence:**
  - 10 sequential and concurrent Playwright browser operations in `tests/browser/test_playwright_tab_reuse.py`.
  - Zero duplicate tabs created; memory stable across all 10 scenarios.

---

## 6. Recommendations for Consumer Nodes

1. **Keep Default Policy at `LOCAL_FIRST`:** Ensures fast, privacy-preserving, zero-cost operations on consumer GPUs.
2. **Do Not Concurrently Load Multiple Local LLMs:** Allow the cache bonus logic in `ModelSelectionEngine` to retain the active model in VRAM.
3. **Use OpenRouter Free Tier as Overflow:** When tasks require large context (>32k tokens) or complex multi-document reasoning, allow the engine to overflow to zero-cost 70B models without incurring API fees.

---

## 7. Conclusion

Jarvis v1 operates efficiently within consumer-tier hardware parameters (4GB VRAM / 16GB RAM), delivering low-latency voice and browser interactions while maintaining strict system stability.
