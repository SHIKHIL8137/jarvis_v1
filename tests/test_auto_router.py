import time

import pytest

from openjarvis.routing import auto_router as r


def M(id, **kw):
    base = dict(local=False, free=False, price_out=1.0, ctx=128_000, tools=True, vision=False,
                reasoning=False, created=time.time() - 90 * 86400, text=id)
    base.update(kw)
    return r.Model(id=id, **base)


CATALOG = [
    M("local-small", local=True, free=True, price_out=0.0, ctx=32_000, text="local-small", size_b=4),
    M("big-premium", price_out=15.0, reasoning=True),
    M("mid-general", price_out=0.8),
    M("cheap-coder", price_out=0.8, text="vendor/super-coder-next code model"),
    M("thinker", price_out=1.0, reasoning=True, text="vendor/deep-thinking-r1"),
    M("pixel", price_out=0.6, vision=True),
    M("no-tools", price_out=0.2, tools=False),
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(r, "_stats", {})
    monkeypatch.setattr(r, "_stats_loaded", True)
    monkeypatch.setattr(r, "discover", lambda force=False: CATALOG)
    monkeypatch.setattr(r, "_run_probe", lambda m: True)  # no network in unit tests


def top(kind, cx=0.5):
    return r.rank(kind, cx)[0][1].id


def test_classification():
    assert r.classify("hi")[0] == "quick"
    assert r.classify("open Docker Desktop")[0] == "agent"
    assert r.classify("write a python function")[0] == "code"
    assert r.classify("what is the latest Node.js version")[0] == "research"
    assert r.classify("explain the trade-offs between microservices and a monolith")[0] == "reasoning"


def test_capabilities_decide_not_names():
    assert top("vision") == "pixel"            # only model that can see
    assert top("code") == "cheap-coder"        # code-oriented model wins on fit
    assert top("reasoning") == "thinker"       # reasoning-capable wins
    assert top("quick") == "local-small"       # free and fast for light chat


def test_tool_support_mandatory_and_budget_respected():
    ids = [m.id for _, m in r.rank("agent", 0.5)]
    assert "no-tools" not in ids
    assert "big-premium" not in ids            # $15/M is over the default $3 budget
    assert "big-premium" not in [m.id for _, m in r.rank("agent", 0.9)]  # 2x budget = $6, still out


def test_failures_demote_a_model():
    assert top("code") == "cheap-coder"
    for _ in range(6):
        r.record("cheap-coder", "code", False, 1.0)
    assert top("code") != "cheap-coder"


def test_observed_slowness_demotes():
    before = dict((m.id, s) for s, m in r.rank("quick", 0.1))
    r.record("local-small", "quick", True, 80.0)  # very slow in practice
    after = dict((m.id, s) for s, m in r.rank("quick", 0.1))
    assert after["local-small"] < before["local-small"]


def test_user_pin_overrides_discovery(tmp_path):
    (tmp_path / "config.toml").write_text('[routing]\ncode = "my/pinned-model"\n')
    assert r.route("write a python function").model == "my/pinned-model"


def test_routes_in_milliseconds():
    r.route("warm up")
    t = time.perf_counter()
    for _ in range(200):
        r.route("write a python function that parses CSV and explain why")
    assert (time.perf_counter() - t) / 200 < 0.005


def test_failed_tool_probe_skips_a_model(monkeypatch):
    # The top-ranked code model cannot actually make a tool call: next best is used.
    monkeypatch.setattr(r, "_run_probe", lambda m: m.id != "cheap-coder")
    assert r.route("write a python function").model != "cheap-coder"


def test_probe_result_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(r, "_run_probe", lambda m: calls.append(m.id) or True)
    r.route("write a python function")
    n = len(calls)
    r.route("write another python function")
    assert n > 0 and len(calls) == n  # second route reuses the cached verdicts
