# Agent Relay 
This is a homework project repo for the [AI Dev Tools Zoomcamp](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main) (2026 cohort) from [DataTalks.Club](https://datatalks.club/).
Initially, it was forked from alexeygrigorev/agent-relay for module 3 homework.
It then evolves over the homework modules.


## SQLite starter, as forked from the instructor

Agent Relay is a small FastAPI service for registering agents, delivering one
task at a time, and recording results. The local starter is self-contained:
SQLite persists the queue and attempts, while workers execute tasks on their own
machines. The included worker deterministically returns `input.upper()`.

### Run it

```bash
uv sync
uv run uvicorn main:app --reload
```

Open <http://127.0.0.1:8000/> for the token-based local dashboard. The default
database is `./agent-relay.db`; set `RELAY_DATABASE_URL` to use another SQLite
file. `GET /health` is a liveness check and `GET /ready` verifies database
connectivity and schema (it queries the real tables, so a wiped volume
reports not-ready instead of passing with zero tables).

Register two identities and send a task:

```bash
alice=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"alice"}')
bob=$(curl -sS -X POST http://127.0.0.1:8000/api/v1/agents \
  -H 'content-type: application/json' -d '{"name":"uppercase"}')
```

The response contains each agent's secret `token` once. Keep it outside source
control. Use `Authorization: Bearer <token>` for all subsequent API calls;
registration is the only unauthenticated endpoint. For a shared installation,
set `RELAY_ENROLLMENT_SECRET` and send it as `X-Enrollment-Secret` when
registering.

### Run the deterministic worker

The worker can register itself and save credentials in a mode-0600 JSON file:

```bash
uv run python main.py worker \
  --base-url http://127.0.0.1:8000 \
  --name uppercase \
  --credentials ./uppercase-credentials.json \
  --worker-id laptop-1
```

For failure/redelivery demonstrations, make local execution intentionally slow
and stop the process after one completion:

```bash
uv run python main.py worker --credentials ./uppercase-credentials.json \
  --slow-seconds 75 --worker-id slow-laptop
```

The worker heartbeats during long work. Killing it leaves the claim leased;
after the 60-second lease expires, another worker can claim the task with a new
token and incremented attempt number. `RELAY_LEASE_SECONDS` and
`RELAY_MAX_ATTEMPTS` are configurable server settings.

An existing credential can also be supplied explicitly (the token is not
written to disk):

```bash
uv run python main.py worker --agent-id agent_123 --token agt_… --worker-id laptop-2
```

### Storage and delivery behavior

`database.py` contains SQLAlchemy models, SQLite WAL setup, and PostgreSQL
support (`DATABASE_URL`/`RELAY_DATABASE_URL` starting `postgresql`).
`storage.py` contains task/claim/recovery operations; routes and request
models are kept in `main.py` and `schemas.py`. SQLite does not provide
PostgreSQL's `FOR UPDATE SKIP LOCKED`, so on SQLite the app serializes every
writer transaction with `BEGIN IMMEDIATE`. On PostgreSQL, `USE_ROW_LOCKS`
switches `storage.py` to real row-level `.with_for_update()` locking on the
specific Task/Attempt rows each operation touches, letting unrelated claims
proceed concurrently — see `docker compose up --build` with `compose.yaml`
for a ready-to-run PostgreSQL stack. The HTTP protocol and lifecycle in
`SPEC.md` are unchanged either way.

Claims are at-least-once and leased for 60 seconds by default. Heartbeats extend
an active lease. A completion or failure must include the recipient's bearer
token and claim token. Repeating the exact terminal request with that claim
token is idempotent; a stale token or different result receives `409`.

### Verify

The test suite covers the main protocol, sender/recipient access boundaries,
hashed claim-token behavior, idempotent terminal retries, concurrent claims,
lease expiry before and after recovery, pagination/error shape, and dashboard
asset serving:

```bash
uv run pytest -q
```

Tests default to a scratch database at `/tmp/agent-relay-test.db` so they
don't reset your dev server's `./agent-relay.db`. The fixture drops and
recreates all tables on whatever `RELAY_DATABASE_URL` points at, so stop
the dev server first or set `RELAY_DATABASE_URL` to a scratch file before
running tests against another database.

This starter intentionally does not include external brokers or an LLM.
Docker, PostgreSQL, Kubernetes, and CI/CD are this fork's homework
additions — see the HW3 status section below.

## Status after completion of homework 3 (HW3)

HW3 asked for four things on top of the starter above: containerize the app,
replace SQLite with PostgreSQL, deploy to Kubernetes, and wire a CI/CD
pipeline that only deploys if tests pass. All four are done and verified
against a real container, a real PostgreSQL instance, and a real (local)
Kubernetes cluster — not just written and assumed to work.

### What's new

- **`Dockerfile`, `.dockerignore`** — builds the app as `agent-relay:local`.
  Multi-layer: dependencies (from `uv.lock`) are installed before app code is
  copied in, so code-only changes don't invalidate the dependency layer.
  `uvicorn` binds `--host 0.0.0.0` so published ports are actually reachable
  from outside the container.
- **`database.py` / `storage.py` — PostgreSQL support.** SQLite has no
  `SELECT ... FOR UPDATE SKIP LOCKED`, so the starter serializes every writer
  through one `BEGIN IMMEDIATE` transaction. On PostgreSQL (`USE_ROW_LOCKS`,
  true whenever `DATABASE_URL`/`RELAY_DATABASE_URL` starts `postgresql`),
  claim/heartbeat/terminal/recovery instead take row-level
  `.with_for_update()` locks on the specific Task/Attempt rows they touch —
  in a consistent Task-then-Attempt order everywhere, so a heartbeat racing a
  lease-expiry recovery pass waits instead of deadlocking. Verified against
  real PostgreSQL: the starter's 16-thread concurrent-claim test and its
  lease-expiry-before/after-recovery test, run 5× to rule out flakiness,
  10/10 clean. The SQLite path is unchanged and still fully passes its own
  suite.
- **`compose.yaml`** — Agent Relay + a `postgres` service (named exactly
  that; it's also the hostname the app's `DATABASE_URL` resolves via Compose's
  internal DNS), a named volume for durability, a healthcheck gating the
  app's startup.
- **`k8s/`** — manifests for a local [kind](https://kind.sigs.k8s.io/)
  cluster: `Deployment` + `Service` + `PersistentVolumeClaim` for PostgreSQL
  (`strategy: Recreate`, since a second pod can't mount the same
  `ReadWriteOnce` volume while the first still holds it), `Deployment` +
  `Service` for the app (`imagePullPolicy: Never` — the image only exists on
  the node via `kind load docker-image`, so this fails loudly instead of
  silently trying a registry pull), an `initContainer` blocking app startup
  until PostgreSQL answers (Kubernetes has no Compose-style `depends_on`),
  and `readinessProbe`/`livenessProbe` on `/ready` and `/health`.
- **`test_q2_integration.py`** — SPEC.md's first acceptance scenario (register
  two agents, send a task, claim, complete, sender reads the result) as a
  real API test, not a mock. `RELAY_BASE_URL` unset runs it in-process against
  the ASGI app; set, it drives a live HTTP endpoint instead — the same file
  verified the SQLite dev server, the Docker container, the Compose stack,
  the kind deployment, and CI, unchanged.
- **`.github/workflows/ci.yml`, `.actrc`** — two jobs. `test` runs the
  starter's suite and `test_q2_integration.py` against a throwaway
  `postgres:16-alpine` service container (never the persistent one the
  cluster runs). `deploy` (`needs: test`, so it never starts if a test
  failed) builds a uniquely-tagged image, loads it into kind, applies
  `k8s/`, and `kubectl set image` + `kubectl rollout status` to roll out and
  wait for it. Runs locally with [act](https://nektosact.com/) against a real
  kind cluster; `.actrc` pins the runner image act uses.

### Run it

```bash
# Docker
docker build -t agent-relay:local .
docker run -d -p 8000:8000 agent-relay:local

# Docker Compose + PostgreSQL
docker compose up --build

# Kubernetes (kind)
kind create cluster --name agent-relay
docker build -t agent-relay:local .
kind load docker-image agent-relay:local --name agent-relay
kubectl apply -f k8s/
kubectl port-forward svc/agent-relay 8000:8000

# CI/CD, locally
act push
```

`http://127.0.0.1:8000/` serves the dashboard in every case above.

### Notes

- Credentials in `compose.yaml` and `k8s/secret.yaml` are fixed, documented
  local-only defaults, not real secrets — this PostgreSQL is never reachable
  outside its own Docker/Kubernetes network. Override them for anything
  beyond local use.
- The kind cluster and its loaded image are local, disposable infrastructure,
  not part of this repo's history — `kind delete cluster --name agent-relay`
  removes them entirely; every step to rebuild them is one of the commands
  above.
