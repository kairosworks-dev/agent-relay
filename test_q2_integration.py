"""Acceptance scenario 1 from SPEC.md as an API integration test.

    Register two agents. One sends a task; the other claims and completes it;
    the sender reads the result.

The test drives the public HTTP API only and lets the service use its real
database.  It runs in two modes so the same file can verify every deployment
target in this homework:

``RELAY_BASE_URL`` set
    Talk to an already running relay over the network -- a container, the
    Compose stack, or a ``kubectl port-forward`` into kind.
``RELAY_BASE_URL`` unset
    Start the ASGI app in-process against whatever ``RELAY_DATABASE_URL``
    points at (SQLite file by default).

Nothing is dropped or truncated: agents are registered with unique names on
every run, so the test is safe against a live stack.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest


BASE_URL = os.getenv("RELAY_BASE_URL")
ENROLLMENT_SECRET = os.getenv("RELAY_ENROLLMENT_SECRET") or os.getenv("ENROLLMENT_SECRET")


@pytest.fixture(scope="module")
def client():
    if BASE_URL:
        with httpx.Client(base_url=BASE_URL.rstrip("/"), timeout=30) as http_client:
            yield http_client
        return
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as test_client:
        yield test_client


def register(client, name: str) -> tuple[str, dict[str, str]]:
    headers = {"X-Enrollment-Secret": ENROLLMENT_SECRET} if ENROLLMENT_SECRET else {}
    response = client.post("/api/v1/agents", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["agent_id"], {"Authorization": f"Bearer {body['token']}"}


def test_service_is_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json() == {"status": "ready"}


def test_sender_reads_completed_result_after_recipient_submits(client):
    run = uuid.uuid4().hex[:8]
    _, alice = register(client, f"alice-{run}")
    recipient_id, uppercase = register(client, f"uppercase-{run}")

    # 1. The sender submits a task addressed to the recipient.
    created = client.post(
        "/api/v1/tasks",
        json={"to": recipient_id, "input": f"hello homework three {run}"},
        headers=alice,
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "queued"

    # Before anyone claims it the sender sees a queued task with no attempts.
    queued = client.get(f"/api/v1/tasks/{task_id}", headers=alice).json()
    assert queued["status"] == "queued"
    assert queued["output"] is None
    assert queued["attempt_count"] == 0

    # 2. The recipient claims it and receives a leased claim token.
    claimed = client.post(
        "/api/v1/tasks/claim",
        json={"worker_id": f"pytest-{run}", "wait_seconds": 5},
        headers=uppercase,
    )
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["task_id"] == task_id
    assert claim["attempt"] == 1
    assert claim["input"] == f"hello homework three {run}"
    assert claim["claim_token"].startswith("clm_")

    # While the lease is held the sender sees the task as processing.
    processing = client.get(f"/api/v1/tasks/{task_id}", headers=alice).json()
    assert processing["status"] == "processing"
    assert processing["output"] is None
    assert processing["attempt_count"] == 1

    # 3. The recipient does the work locally and submits the result.
    output = claim["input"].upper()
    completed = client.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"claim_token": claim["claim_token"], "output": output},
        headers=uppercase,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json() == {"task_id": task_id, "status": "completed"}

    # 4. The sender reads the result: this is the status Question 2 asks about.
    final = client.get(f"/api/v1/tasks/{task_id}", headers=alice).json()
    assert final["status"] == "completed"
    assert final["output"] == output
    assert final["error"] is None
    assert final["finished_at"] is not None

    # The durable attempt record backs the result up in the database.
    attempts = client.get(f"/api/v1/tasks/{task_id}/attempts", headers=alice).json()["items"]
    assert len(attempts) == 1
    assert attempts[0]["attempt"] == 1
    assert attempts[0]["outcome"] == "completed"
    assert attempts[0]["worker_id"] == f"pytest-{run}"

    # The task is visible from both sides through the listing endpoints.
    sent = client.get("/api/v1/tasks?direction=sent&status=completed&limit=100", headers=alice).json()
    assert task_id in {item["task_id"] for item in sent["items"]}
    received = client.get("/api/v1/tasks?direction=received&status=completed&limit=100", headers=uppercase).json()
    assert task_id in {item["task_id"] for item in received["items"]}
