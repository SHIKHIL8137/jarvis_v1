"""Automatic model selection for the "auto" model: no model names are hard-coded.

1. **Discover** what is available right now: local Ollama models (with the
   capabilities Ollama reports) and, if an OpenRouter key exists, OpenRouter's
   live catalogue (tools, vision, reasoning, context size, price, age).
2. **Classify** the message (code, tools/apps, research, reasoning, vision, quick)
   with cheap regex signals plus the existing complexity score.
3. **Score** every capable model for that kind of work: quality proxy, cost,
   speed and fit, scaled by how reliable and fast it has actually been for you.
4. **Learn**: failures and response times of auto-routed requests are recorded
   and shift future choices (``~/.openjarvis/routing_stats.json``).

Optional ``[routing]`` section in ``~/.openjarvis/config.toml``::

    max_price_per_m = 3.0     # max USD per million output tokens (default 3.0)
    allow_free = true         # consider ":free" models (they are rate limited)
    code = "some/model"       # pin a kind to one model (overrides discovery)
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import tomllib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx

from openjarvis.core.paths import get_config_dir

KINDS = ("quick", "vision", "code", "agent", "reasoning", "research")
_CACHE_SECS = 3600
_lock = threading.Lock()
_catalog: dict = {"at": 0.0, "models": []}


@dataclass
class Model:
    id: str
    local: bool
    free: bool
    price_out: float  # USD per million output tokens (0 for local/free)
    ctx: int
    tools: bool
    vision: bool
    reasoning: bool
    created: float
    text: str  # lower-cased name + description for fit matching
    size_b: float = 0.0  # parameters in billions (local models)


@dataclass
class RouteDecision:
    model: str
    kind: str
    reason: str
    micros: int
    alternatives: List[str] = field(default_factory=list)


# --- configuration ----------------------------------------------------------


def _settings() -> dict:
    try:
        with open(get_config_dir() / "config.toml", "rb") as f:
            return tomllib.load(f).get("routing", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


# --- discovery --------------------------------------------------------------


def _ollama_models() -> List[Model]:
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    host = host if host.startswith("http") else f"http://{host}"
    try:
        rows = httpx.get(f"{host}/api/tags", timeout=2.0).json().get("models", [])
    except Exception:
        return []
    out = []
    for m in rows:
        caps = m.get("capabilities") or []
        if "completion" not in caps:  # embedding-only models etc.
            continue
        size = re.search(r"([\d.]+)\s*B", str(m.get("details", {}).get("parameter_size", "")), re.I)
        out.append(Model(
            id=m["name"], local=True, free=True, price_out=0.0,
            ctx=int(m.get("details", {}).get("context_length") or 8192),
            tools="tools" in caps, vision="vision" in caps, reasoning="thinking" in caps,
            created=0.0, text=m["name"].lower(), size_b=float(size.group(1)) if size else 4.0,
        ))
    return out


def _openrouter_models() -> List[Model]:
    from openjarvis.server.cloud_router import _load_keys

    if "OPENROUTER_API_KEY" not in _load_keys():
        return []
    try:
        rows = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0).json().get("data", [])
    except Exception:
        return []
    soon = time.time() + 14 * 86400
    out = []
    for m in rows:
        arch, pr = m.get("architecture") or {}, m.get("pricing") or {}
        try:
            p_out = float(pr.get("completion", "-1")) * 1e6
            p_in = float(pr.get("prompt", "-1"))
        except ValueError:
            continue
        if p_out < 0 or p_in < 0 or m["id"].startswith("openrouter/"):  # meta/variable-priced routers
            continue
        if m["id"].endswith(":batch"):  # asynchronous batch endpoints, not for live chat
            continue
        if arch.get("output_modalities") != ["text"]:
            continue
        exp = m.get("expiration_date")
        if exp and time.mktime(time.strptime(exp[:10], "%Y-%m-%d")) < soon:
            continue
        sp = m.get("supported_parameters") or []
        out.append(Model(
            id="openrouter/" + m["id"], local=False, free=p_out == 0, price_out=p_out,
            ctx=int(m.get("context_length") or 0), tools="tools" in sp,
            vision="image" in (arch.get("input_modalities") or []), reasoning="reasoning" in sp,
            created=float(m.get("created") or 0),
            text=f"{m['id']} {m.get('name', '')} {m.get('description', '')}".lower(),
        ))
    return out


def discover(force: bool = False) -> List[Model]:
    """All models usable right now (cached for an hour)."""
    with _lock:
        if force or not _catalog["models"] or time.time() - _catalog["at"] > _CACHE_SECS:
            models = _ollama_models() + _openrouter_models()
            if models:  # keep the old list if discovery briefly fails
                _catalog.update(at=time.time(), models=models)
        return list(_catalog["models"])


# --- learning from real use -------------------------------------------------

_stats: dict = {}
_stats_loaded = False


def _stats_file():
    return get_config_dir() / "routing_stats.json"


def _load_stats() -> None:
    global _stats, _stats_loaded
    if not _stats_loaded:
        try:
            _stats = json.loads(_stats_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _stats = {}
        _stats_loaded = True


def record(model: str, kind: str, ok: bool, secs: float) -> None:
    """Remember how an auto-routed request went (reliability and speed)."""
    with _lock:
        _load_stats()
        s = _stats.setdefault(f"{model}|{kind}", {"ok": 0, "fail": 0})
        s["ok" if ok else "fail"] += 1
        if ok:
            lat = _stats.setdefault(f"{model}|latency", {"avg": secs, "n": 0})
            lat["avg"] = round(lat["avg"] * 0.8 + secs * 0.2, 2) if lat["n"] else round(secs, 2)
            lat["n"] += 1
        try:
            _stats_file().parent.mkdir(parents=True, exist_ok=True)
            _stats_file().write_text(json.dumps(_stats), encoding="utf-8")
        except OSError:
            pass


def _reliability(m: Model, kind: str) -> float:
    """Success rate for this model on this kind of work, starting from a prior.

    Unproven free cloud models start lower (rate limits, previews); repeated
    failures sink any model, successes lift it toward 1.
    """
    with _lock:
        _load_stats()
        s = _stats.get(f"{m.id}|{kind}", {"ok": 0, "fail": 0})
    prior = 0.65 if (m.free and not m.local) else 0.85
    weight = 3.0
    return (s["ok"] + weight * prior) / (s["ok"] + s["fail"] + weight)


def _speed(m: Model) -> float:
    """0..1, from observed latency when known, else a size/price prior."""
    with _lock:
        _load_stats()
        lat = _stats.get(f"{m.id}|latency")
    if lat:
        return 1.0 / (1.0 + lat["avg"] / 10.0)  # 0s -> 1, 10s -> 0.5, 60s -> 0.14
    return 0.5 if m.local else 0.8


# --- classification ---------------------------------------------------------

_AGENT = re.compile(
    r"\b(open|launch|click|screenshot|screen|window|desktop|app|folder|file|files|directory|"
    r"project|repo|repository|git|run|install|docker|terminal|browser|website|"
    r"go to|on my (pc|device|computer)|in my (pc|device|computer))\b|[a-z]:\\",
    re.I,
)
_CODE = re.compile(
    r"```|\b(code|function|bug|fix|refactor|implement|class|api|endpoint|compile|error|"
    r"stack ?trace|regex|sql|python|javascript|typescript|react|css|html|unit test|tests?)\b",
    re.I,
)
_RESEARCH = re.compile(
    r"\b(latest|news|today|current(ly)?|price of|search( the)? web|google|look up|research|"
    r"find out|weather|who is|release date)\b",
    re.I,
)
_REASON = re.compile(
    r"\b(why|explain|prove|analy[sz]e|compare|design|architecture|strategy|derive|"
    r"optimi[sz]e|trade-?offs?|pros and cons|step by step)\b",
    re.I,
)
_FIT_CODE = re.compile(r"\b(cod(e|er|ing)|swe|developer|program)", re.I)
_FIT_REASON = re.compile(r"\b(r1|thinking|reasoning|reasoner|think)\b", re.I)


def classify(query: str, has_images: bool = False) -> tuple[str, str, float]:
    """Return (kind, reason, complexity 0..1)."""
    from openjarvis.learning.routing.complexity import score_complexity

    cx = score_complexity(query).score
    if has_images:
        return "vision", "message has an image", cx
    if _AGENT.search(query):
        return "agent", "needs tools: files, apps, desktop or browser", cx
    if _CODE.search(query):
        return "code", "coding task", cx
    if _RESEARCH.search(query):
        return "research", "needs current information", cx
    if cx >= 0.35 or _REASON.search(query):
        return "reasoning", f"complex question (score {cx:.2f})", cx
    return "quick", f"simple message (score {cx:.2f})", cx


# --- scoring ----------------------------------------------------------------

# (quality, cost, speed) weights per kind: light chat favours cheap and fast,
# serious work favours capability.
_WEIGHTS = {
    "quick": (0.25, 0.40, 0.35), "vision": (0.50, 0.30, 0.20), "research": (0.50, 0.35, 0.15),
    # agent/code run many sequential calls per task, so latency matters as much as price
    "code": (0.55, 0.15, 0.30), "agent": (0.55, 0.15, 0.30), "reasoning": (0.65, 0.20, 0.15),
}
_NOW = time.time()


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _quality(m: Model) -> float:
    # shortcut: price and recency stand in for capability (no benchmark data is
    # published in the catalogues); revisit if a benchmark source becomes available.
    recency = _clamp(1 - (_NOW - m.created) / (2 * 365 * 86400)) if m.created else 0.3
    if m.local:
        return 0.5 * _clamp(math.log2(max(m.size_b, 1)) / math.log2(70))
    if m.free:
        return 0.30 + 0.10 * recency + (0.10 if m.reasoning else 0.0)
    price = _clamp((math.log10(m.price_out + 0.01) - math.log10(0.05)) / (math.log10(15) - math.log10(0.05)))
    return 0.6 * price + 0.25 * recency + 0.15 * _clamp(math.log2(max(m.ctx, 8192) / 8192) / 7)


def _cost_score(m: Model) -> float:
    if m.price_out <= 0:
        return 1.0
    return 1 - _clamp((math.log10(m.price_out + 0.01) - math.log10(0.05)) / (math.log10(15) - math.log10(0.05)))


def _fit(m: Model, kind: str) -> float:
    if kind == "quick" and m.local:
        return 0.06  # light chat: free and private, unless it proves slow (see _speed)
    if kind == "code" and _FIT_CODE.search(m.text):
        return 0.30
    if kind == "reasoning":
        return (0.15 if m.reasoning else 0) + (0.15 if _FIT_REASON.search(m.text) else 0)
    if kind == "research" and m.ctx >= 128_000:
        return 0.10
    return 0.0


def rank(kind: str, complexity: float, models: Optional[List[Model]] = None) -> List[tuple[float, Model]]:
    cfg = _settings()
    budget = float(cfg.get("max_price_per_m", 3.0)) * (2 if complexity >= 0.6 else 1)
    allow_free = bool(cfg.get("allow_free", True))
    wq, wc, ws = _WEIGHTS[kind]
    scored = []
    for m in models if models is not None else discover():
        # Every chat goes through the tool-using agent, so tool support is mandatory.
        if not m.tools or m.ctx < 16_000 and not m.local:
            continue
        if kind == "vision" and not m.vision:
            continue
        if m.price_out > budget or (m.free and not m.local and not allow_free):
            continue
        base = wq * _quality(m) + wc * _cost_score(m) + ws * _speed(m) + _fit(m, kind)
        scored.append((base * _reliability(m, kind) ** 2, m))
    return sorted(scored, key=lambda t: -t[0])


# --- capability probe -------------------------------------------------------

_PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "report_number",
        "description": "Report the number the user asked for.",
        "parameters": {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        },
    },
}]
_PROBE_TTL = 7 * 86400


def _run_probe(m: Model) -> bool:
    """Ask the model to make one tool call; True only if it does so correctly."""
    from openjarvis.core.config import load_config
    from openjarvis.core.types import Message, Role
    from openjarvis.engine._discovery import get_engine
    from openjarvis.server.cloud_router import is_cloud_model

    cfg = load_config()
    found = get_engine(cfg, "cloud") if is_cloud_model(m.id) else get_engine(cfg, model=m.id)
    if not found:
        return False
    res = found[1].generate(
        [Message(role=Role.USER, content="Call report_number with n=7. Do not answer in text.")],
        model=m.id, temperature=0.0, max_tokens=80, tools=_PROBE_TOOL,
    )
    for tc in res.get("tool_calls") or []:
        name = tc.get("name") or (tc.get("function") or {}).get("name")
        args = tc.get("arguments") or (tc.get("function") or {}).get("arguments") or "{}"
        try:
            n = json.loads(args).get("n") if isinstance(args, str) else args.get("n")
        except ValueError:
            continue
        if name == "report_number" and str(n) == "7":
            return True
    return False


def _verify(models: List[Model], timeout: float = 12.0) -> Dict[str, bool]:
    """Probe cloud candidates not checked this week, in parallel; local models are trusted."""
    from concurrent.futures import ThreadPoolExecutor

    now = time.time()
    verdict: Dict[str, bool] = {}
    todo = []
    with _lock:
        _load_stats()
        for m in models:
            if m.local:
                verdict[m.id] = True
                continue
            hit = _stats.get(f"{m.id}|probe")
            if hit and now - hit["at"] < _PROBE_TTL:
                verdict[m.id] = bool(hit["ok"])
            else:
                todo.append(m)
    if todo:
        pool = ThreadPoolExecutor(max_workers=len(todo))
        futures = {m.id: (pool.submit(_run_probe, m), time.time()) for m in todo}
        for mid, (fut, t0) in futures.items():
            try:
                ok = bool(fut.result(timeout=max(0.1, timeout - (time.time() - t0))))
            except Exception:
                ok = False
            verdict[mid] = ok
            with _lock:
                _stats[f"{mid}|probe"] = {"ok": ok, "at": now, "secs": round(time.time() - t0, 1)}
        pool.shutdown(wait=False)
        with _lock:
            try:
                _stats_file().write_text(json.dumps(_stats), encoding="utf-8")
            except OSError:
                pass
    return verdict


def _pick(ranked: List[tuple[float, Model]]) -> Optional[Model]:
    """Best candidate that really passes the tool-call probe (checks the top few at a time)."""
    for i in range(0, min(len(ranked), 9), 3):
        batch = [m for _, m in ranked[i:i + 3]]
        verdict = _verify(batch)
        for m in batch:
            if verdict.get(m.id):
                return m
    return ranked[0][1] if ranked else None



def model_for(kind: str, complexity: float = 0.5) -> str:
    pinned = _settings().get(kind)
    if pinned:
        return str(pinned)
    best = _pick(rank(kind, complexity))
    if best:
        return best.id
    if kind != "quick":
        return model_for("quick", complexity)
    local = [m.id for m in discover() if m.local]
    return local[0] if local else "qwen3:4b-instruct"


def route(query: str, has_images: bool = False) -> RouteDecision:
    """Pick a model. First use of a new cloud model includes a one-off probe (seconds)."""
    t0 = time.perf_counter()
    kind, reason, cx = classify(query, has_images)
    pinned = _settings().get(kind)
    ranked = [] if pinned else rank(kind, cx)
    best = None if pinned else _pick(ranked)
    model = str(pinned) if pinned else (best.id if best else model_for("quick", cx))
    alts = [f"{m.id} ({s:.2f})" for s, m in ranked[:4]]
    return RouteDecision(model, kind, reason, int((time.perf_counter() - t0) * 1e6), alts)
