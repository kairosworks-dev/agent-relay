# Homework 3: Containerize and Deploy

In this homework, you'll deploy Agent Relay. It's is a small messaging system for software agents.

An agent sends a task to another agent, a worker claims the task, and the worker acknowledges the result. The database stores the messages and their delivery attempts. A small dashboard lets you watch the message lifecycle.

You'll use your coding agent to test and containerize Agent Relay. Then you'll deploy it to a local Kubernetes cluster with [kind](https://kind.sigs.k8s.io/) (Kubernetes in Docker).

The starter project already contains the API and dashboard.

You don't need a cloud account, LLM API key, or external message broker. Everything runs on your machine. Ask your agent to install and verify each tool when you need it.


## Question 1: Understand the project

Fork the Agent Relay starter repository from [here](https://github.com/alexeygrigorev/agent-relay).

Ask your agent to run the project. Try to understand it and experiment with it.

Which description matches the project's architecture?

- Agents exchange tasks directly with each other.
- Agents claim tasks from a DB through an HTTP API.
- Agents consume tasks from a message broker.
- The browser stores and executes tasks.

## Question 2: Register agents and test the task flow

Ask your coding agent to read `SPEC.md` (in the starter repo root) and try its first acceptance scenario with your local Agent Relay:

Register two agents and have them exchange a task and its result.

Check the result in the dashboard. Then ask your coding agent to turn this flow into an API integration test against the real API and DB. Run the test and confirm it passes.

Which task status does the sender see after the recipient submits its result?

- `queued`
- `processing`
- `completed`
- `delivered`

## Question 3: Containerization

Ask your coding agent to create a Dockerfile for Agent Relay. Build the image as `agent-relay:local` and run it with the API port published to your machine.

Tip: run uvicorn with `--host 0.0.0.0` inside the container, otherwise `-p` looks broken (uvicorn defaults to `127.0.0.1`).

Open the dashboard and repeat the task flow from Question 2 against the containerized API.

Which Docker option publishes a container's port to your machine?

- `--expose`
- `-p`
- `-v`
- `--name`

## Question 4: Docker Compose and PostgreSQL

Ask your coding agent to replace SQLite with PostgreSQL and create a `compose.yaml` that runs Agent Relay and PostgreSQL together. Name the database service `postgres`.

Start the stack:

```bash
docker compose up --build
```

Run the integration test from Question 2 against the Compose stack and check the result in the dashboard. Confirm that the app stores its data in PostgreSQL.

Which hostname should the API use to connect to the `postgres` service in Docker Compose?

- `localhost`
- `postgres`
- `host.docker.internal`
- `0.0.0.0`

## Question 5: Deploy to Kubernetes

Ask your coding agent to install [kind](https://kind.sigs.k8s.io/) and kubectl if needed, then create a local Kubernetes cluster.

Create manifests in `k8s/` for Agent Relay and PostgreSQL, including Services, persistent DB storage, and readiness checks. Load your Docker image into kind and deploy the application.

Check that the pods are ready. Open the dashboard through port forwarding and verify the task flow from Question 2.

Which Kubernetes resource keeps the requested number of application replicas running and manages updates?

- Service
- ConfigMap
- Deployment
- Secret


## Question 6: CI/CD

Ask your coding agent to create `.github/workflows/ci.yml` that runs the starter's tests and your integration test against PostgreSQL, builds a new Docker image, and deploys it to your kind cluster only if the tests pass.

Run the workflow locally with [act](https://nektosact.com/). Ask your agent to configure access to Docker and the kind cluster, including loading the new image into kind. Use a unique image tag for each version and wait for the rollout to finish.

Change the dashboard heading to `Agent Relay v2` and run the workflow again. Verify that the tests pass and the new heading appears in the deployed dashboard.

What should happen if a test fails in this workflow?

- Deploy the new version and report the failure.
- Keep the existing version running and stop the deployment.
- Delete the existing deployment.
- Deploy the previous image with the new tag.


## Answers / Inputs for the online form

### MC Questions
1. Which description matches the project's architecture? (1 point)
Agents claim tasks from a DB through an HTTP API

2. Which task status does the sender see after the recipient submits its result? (1 point)
Completed

3. Which Docker option publishes a container's port to your machine? (1 point)


4. Which hostname should the API use to connect to the postgres service in Docker Compose? (1 point)


5. Which Kubernetes resource keeps the requested number of application replicas running and manages updates? (1 point)


6. What should happen if a test fails in this workflow? (1 point)


### Further inputs
Homework URL 

Time spent on lectures (hours) (optional) 

Time spent on homework (hours) (optional) 

