import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.capabilities import RuntimeCapabilityRegistry
from openjarvis.engine.router import ModelRouter
from openjarvis.server.security_routes import router as security_router


def test_runtime_capability_registry():
    hw = RuntimeCapabilityRegistry.detect_hardware()
    assert "os" in hw
    assert "cpu_name" in hw
    assert "ram_total_gb" in hw
    assert hw["ram_total_gb"] > 0

    caps = RuntimeCapabilityRegistry.get_capabilities()
    assert caps["system"] == "OpenJarvis"
    assert "hardware" in caps
    assert "tools" in caps
    assert "speech" in caps
    assert "security" in caps
    assert caps["security"]["approval_gateway_enforced"] is True


def test_model_router():
    router = ModelRouter(default_model="qwen2.5:3b", vram_limit_mb=4096)

    # Intent detection
    is_cmd, action, target = router.is_model_intent("Switch to claude-3-5-sonnet")
    assert is_cmd is True
    assert action == "switch"
    assert target == "claude-3-5-sonnet"

    is_cmd, action, target = router.is_model_intent("which model are you using?")
    assert is_cmd is True
    assert action == "query"

    is_cmd, _, _ = router.is_model_intent("Hello, can you help me write python code?")
    assert is_cmd is False

    # Switching
    res = router.switch_model("deepseek-r1:1.5b")
    assert res["success"] is True
    assert router.active_model == "deepseek-r1:1.5b"
    assert res["warning"] is None

    # Low VRAM warning for huge model
    res_huge = router.switch_model("llama3:70b")
    assert res_huge["success"] is True
    assert res_huge["warning"] is not None
    assert "exceed your 4 GB VRAM limit" in res_huge["warning"]


def test_security_routes_endpoints():
    app = FastAPI()
    app.include_router(security_router)
    client = TestClient(app)

    # Capabilities endpoint
    resp = client.get("/v1/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert data["system"] == "OpenJarvis"
    assert "hardware" in data

    # Models endpoints
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    models_data = resp.json()
    assert "active_model" in models_data
    assert "available_models" in models_data

    resp = client.get("/v1/models/current")
    assert resp.status_code == 200
    assert "active_model" in resp.json()

    resp = client.post("/v1/models/switch", json={"model": "qwen2.5:3b"})
    assert resp.status_code == 200
    assert resp.json()["active_model"] == "qwen2.5:3b"

    # Actions endpoint
    resp = client.get("/v1/security/actions")
    assert resp.status_code == 200
    assert "actions" in resp.json()
