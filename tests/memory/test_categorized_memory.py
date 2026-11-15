import os
import tempfile
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI

from openjarvis.memory.store import LocalFactStore, Fact
from openjarvis.server.api_routes import memory_router


@pytest.fixture
def temp_facts_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "facts.jsonl"


def test_categorized_fact_store(temp_facts_path):
    store = LocalFactStore(path=temp_facts_path)
    
    # Add categorized facts
    assert store.add("User prefers dark mode", category="preferences")
    assert store.add("Project Jarvis uses FastAPI and Kokoro", category="projects")
    assert store.add("Ryzen 5 5600H CPU with GTX 1650 GPU", category="environment")
    assert store.add("Always ask before deleting files", category="decisions", pinned=True)
    
    # Deduplication
    assert not store.add("User prefers dark mode", category="preferences")
    
    # List all
    all_facts = store.list()
    assert len(all_facts) == 4
    
    # Filter by category
    pref_facts = store.list_categorized(category="preferences")
    assert len(pref_facts) == 1
    assert "dark mode" in pref_facts[0].text
    
    # Filter by query
    jarvis_facts = store.list_categorized(query="FastAPI")
    assert len(jarvis_facts) == 1
    assert jarvis_facts[0].category == "projects"
    
    # Update fact
    target_id = pref_facts[0].id
    assert store.update_fact(target_id, text="User strongly prefers dark mode with high contrast")
    updated = [f for f in store.list() if f.id == target_id][0]
    assert "high contrast" in updated.text
    
    # Pin/unpin
    assert store.pin_fact(target_id, pinned=True)
    assert [f for f in store.list() if f.id == target_id][0].pinned is True
    
    # Delete fact
    assert store.delete_fact(target_id)
    assert len(store.list()) == 3
    assert not store.delete_fact("nonexistent_id")


def test_memory_router_facts(temp_facts_path):
    app = FastAPI()
    app.include_router(memory_router)
    
    store = LocalFactStore(path=temp_facts_path)
    app.state.fact_store = store
    client = TestClient(app)
    
    # Create fact
    resp = client.post("/v1/memory/facts", json={
        "text": "I like dark mode",
        "category": "preferences",
        "pinned": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    fact_id = data["fact"]["id"]
    assert data["fact"]["pinned"] is True
    
    # Deduplicate
    resp = client.post("/v1/memory/facts", json={
        "text": "I like dark mode",
        "category": "preferences",
    })
    assert resp.json()["status"] == "deduplicated"
    
    # List facts
    resp = client.get("/v1/memory/facts?category=preferences")
    assert resp.status_code == 200
    facts = resp.json()["facts"]
    assert len(facts) == 1
    assert facts[0]["id"] == fact_id
    
    # Update fact
    resp = client.put(f"/v1/memory/facts/{fact_id}", json={
        "text": "I prefer dark mode always",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"
    
    # Pin / unpin
    resp = client.post(f"/v1/memory/facts/{fact_id}/pin", json={"pinned": False})
    assert resp.status_code == 200
    assert resp.json()["pinned"] is False
    
    # Delete fact
    resp = client.delete(f"/v1/memory/facts/{fact_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    
    # Confirm deletion
    resp = client.get("/v1/memory/facts")
    assert len(resp.json()["facts"]) == 0
