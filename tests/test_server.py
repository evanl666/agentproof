import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agentproof.server import ProjectStore, make_handler


def test_store_create_and_list(tmp_path):
    store = ProjectStore(tmp_path)
    assert store.list_projects() == []
    rec = store.create_project("Refund bot", pack="support")
    assert rec["id"].startswith("refund-bot-")
    assert rec["total"] > 0
    listed = store.list_projects()
    assert len(listed) == 1
    assert listed[0]["name"] == "Refund bot"


def test_store_simulate_and_autofix(tmp_path):
    store = ProjectStore(tmp_path)
    rec = store.create_project("Fintech", pack="fintech")
    assert not rec["score"]["shippable"]  # naive graph
    fixed = store.autofix(rec["id"])
    assert fixed["score"]["shippable"]
    assert fixed["passed"] == fixed["total"]
    assert fixed["fixes"]
    assert fixed["policy"]["open"] == 0


def test_store_persists_to_disk(tmp_path):
    store = ProjectStore(tmp_path)
    rec = store.create_project("Persisted", pack="support")
    reloaded = ProjectStore(tmp_path)
    assert reloaded.get_project(rec["id"])["name"] == "Persisted"


def test_policy_library_attaches_to_new_project(tmp_path):
    store = ProjectStore(tmp_path)
    policy = store.add_policy(
        "No PII egress",
        [{"id": "pii", "kind": "pii_egress", "description": "never send PII externally", "params": {}}],
    )
    rec = store.create_project(
        "Prose agent",
        spec_text="Build a support agent that answers questions.",
        policy_ids=[policy["id"]],
    )
    kinds = {c["kind"] for c in rec["spec"]["constraints"]}
    assert "pii_egress" in kinds


def test_delete_project(tmp_path):
    store = ProjectStore(tmp_path)
    rec = store.create_project("Temp", pack="support")
    store.delete_project(rec["id"])
    assert store.list_projects() == []


@pytest.fixture
def server(tmp_path):
    store = ProjectStore(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_dashboard_and_api(server):
    with urllib.request.urlopen(server + "/") as res:
        assert b"Team dashboard" in res.read()

    def post(path, payload):
        req = urllib.request.Request(
            server + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    rec = post("/api/projects", {"name": "Via API", "pack": "healthcare"})
    assert rec["name"] == "Via API"

    with urllib.request.urlopen(server + "/api/projects") as res:
        projects = json.loads(res.read())
    assert len(projects) == 1

    fixed = post(f"/api/projects/{rec['id']}/autofix", {})
    assert fixed["score"]["shippable"]
