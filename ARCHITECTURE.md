# AI Agent Team — Microservices Architecture

## Overview

A fully autonomous AI engineering team built as true independent microservices. Each agent runs in its own Docker container, maintains persistent memory, and communicates via Redis Streams. The Manager AI acts as the IT Product Manager — it receives goals, breaks them into tasks, and delegates to the right specialist.

```
User / Dashboard
      │
      ▼
  Gateway :8000  (single entry point)
      │
      ├──▶ Manager   :8001  (IT Product Manager — orchestrator)
      ├──▶ Developer :8002  (Senior Software Engineer)
      ├──▶ DevOps    :8003  (Platform / Infrastructure Engineer)
      ├──▶ QA        :8004  (Senior QA Engineer)
      ├──▶ Support   :8005  (Customer Support Engineer)
      └──▶ Docs      :8006  (Technical Documentation Engineer)

Shared infrastructure:
  Redis  :6379  (message bus + per-agent memory)
  Postgres :5432 (shared task/ticket database)
```

---

## Design Principles

**True Independence** — each agent is a separate Python process, separate Docker container, separate crash domain. A QA agent crash does not affect the Developer.

**Reliable Messaging** — Redis Streams (not pub/sub). Messages persist even if the consumer is offline. Consumer groups ensure exactly-once delivery. ACK after processing.

**Plug-and-Play** — every LLM provider, tool, memory backend, and notifier is swappable via Python Protocols. Swap Groq → OpenAI for any agent by changing one env var.

**Persistent Memory** — two-tier memory per agent: working memory (Redis Hash, 7-day TTL) + long-term memory (Redis Sorted Set, top 200 facts ranked by importance). Injected into every LLM call.

**Role Skills** — 8–9 domain-specific best practices loaded into each agent's memory on startup (e.g., Developer knows SOLID + TDD; DevOps knows 12-factor + IaC).

---

## Component Deep-Dive

### Gateway (`services/gateway/main.py` — port 8000)
Single ingress. Routes `/goal` → Manager, `/agent/{role}/*` → respective service. Handles health aggregation for the dashboard.

### Agent Services

| Service   | Port | Provider   | Key Tools                                    |
|-----------|------|------------|----------------------------------------------|
| manager   | 8001 | OpenRouter | create_task, get_tasks, web_search, send_email |
| developer | 8002 | Groq       | code_run, file_write, web_search, create_task |
| devops    | 8003 | Groq       | code_run, file_write, web_browse, create_task  |
| qa        | 8004 | OpenRouter | code_run, file_write, create_task, send_email  |
| support   | 8005 | Gemini     | web_search, file_read, create_task, send_email |
| docs      | 8006 | OpenRouter | web_browse, file_write, file_read              |

### Shared Core (`shared/core/`)

**`config.py`** — Pydantic Settings loaded from environment. Per-role LLM provider routing via `{ROLE}_PROVIDER` env vars.

**`llm.py`** — Unified `LLMClient` dispatching to OpenRouter, Groq, Gemini, or HuggingFace based on config. Returns `ChatCompletion`-compatible responses.

**`agent_base.py`** — `BaseAgent` abstract class with:
- `startup()` — loads skills into Redis, starts stream listener
- `listen()` — async Redis Streams consumer loop
- `run(task_id)` — injects memory + skills into system prompt, runs ReAct loop
- `ask(prompt)` — executes tool-use loop until `done` or max 10 turns

**`messaging.py`** — `MessageBus` wrapping `redis.asyncio`. `publish(role, payload)` adds to `agent:{role}` stream. `consume(role)` yields messages via consumer group with auto-ACK.

**`memory.py`** — `AgentMemory` with:
- Working memory: `HSET agent:{role}:wm key value` (7-day TTL)
- Long-term memory: `ZADD agent:{role}:ltm score fact` (capped at 200)
- Skills: `RPUSH agent:{role}:skills skill_text` (loaded at startup)
- `recall(query)` — keyword-scored retrieval from LTM

**`tool_registry.py`** — `ToolRegistry` singleton. `register(name, schema, executor)` adds a tool. Agents declare `required_tools`; the registry provides OpenAI-compatible schemas and executes calls by name.

**`database.py`** — Postgres task store via psycopg2. JIRA-style ticket IDs (`DEV-001`, `QA-001`, etc.) from `ticket_counters` table. Functions: `create_task`, `get_tasks`, `update_task`, `get_task`.

### Shared Tools (`shared/tools/`)

| Tool          | Description                                              |
|---------------|----------------------------------------------------------|
| web_search    | Serper.dev API — 10 results per query                   |
| web_browse    | httpx + BeautifulSoup — fetch URL, extract text          |
| file_ops      | Sandboxed read/write/list (confined to `/app/sandbox`)   |
| code_sandbox  | Subprocess Python execution with 30s timeout            |
| email_send    | aiosmtplib SMTP — HTML + plain text                     |
| task_tools    | create_task / get_tasks / update_task wrappers           |

---

## Message Flow

```
User sends goal to Gateway POST /goal
  → Gateway proxies to Manager POST /goal
    → ManagerAgent.plan_and_delegate(goal)
      → LLM call: "Break into 2–5 tasks"
      → Tool calls: create_task × N (writes to Postgres, publishes to Redis Stream)
      → Returns plan summary

Redis Stream agent:developer receives task
  → DeveloperAgent._handle_task(task_id)
    → Fetches task from Postgres
    → Runs ReAct loop: reason → tool calls → observe → repeat
    → Updates task status: in_progress → done/failed
    → Publishes result event
    → Stores completion in LTM memory
```

---

## Autonomous Scheduler (Manager service)

APScheduler jobs running inside the Manager container:

| Job             | Schedule          | Description                            |
|-----------------|-------------------|----------------------------------------|
| Daily Plan      | 09:00 every day   | Manager reviews backlog and delegates  |
| Delegation Loop | Every 30 minutes  | Check pending tasks, assign to agents  |
| SLA Check       | Every hour        | Alert on overdue P1/P2 tickets         |
| Daily Report    | 17:00 every day   | Email status summary to stakeholders   |
| Weekly Report   | 08:00 Monday      | Full sprint recap via email            |
| Health Check    | Every 5 minutes   | Verify all agent services are alive    |

---

## Memory Architecture

```
Redis (per agent)
├── agent:{role}:wm          Hash — working memory (7-day TTL)
│   ├── last_task: "DEV-012 completed at 14:32"
│   └── current_sprint: "sprint-3"
│
├── agent:{role}:ltm          Sorted Set — long-term memory
│   ├── score:2.0  "Completed DEV-012: JWT auth middleware"
│   ├── score:1.5  "Used groq/llama3 for code generation"
│   └── (top 200 kept, lowest score evicted)
│
└── agent:{role}:skills       List — role skills (loaded at startup)
    ├── "SOLID principles: Single Responsibility..."
    ├── "Async-first in Python: use async/await..."
    └── "Test-driven development: write test first..."
```

---

## API Reference

All endpoints available via Gateway at `http://localhost:8000`.

### Core Endpoints

```
POST /goal                          Send goal to Manager AI
  Body: { "goal": "string" }
  Returns: { "response": "plan summary", "task_ids": [...] }

GET  /tasks?limit=100               List all tasks (Postgres)
GET  /tasks/{ticket_id}             Get specific task

POST /agent/{role}/ask              Direct question to any agent
  Body: { "message": "string" }

GET  /agent/{role}/health           Health + memory summary
GET  /agent/{role}/memory           Long-term memory contents
POST /agent/{role}/memory/learn     Add skill or memory

GET  /status                        Aggregate team status
GET  /report/daily                  Generate daily report
GET  /report/weekly                 Generate weekly report

GET  /health                        Gateway + all services health
```

### Per-Service Docs

Each service exposes `/docs` (Swagger UI):
- Manager: http://localhost:8001/docs
- Developer: http://localhost:8002/docs
- DevOps: http://localhost:8003/docs
- QA: http://localhost:8004/docs
- Support: http://localhost:8005/docs
- Docs: http://localhost:8006/docs

---

## Running the System

### Quick Start

```bash
# 1. Clone / navigate to project
cd ai-agent-microservices

# 2. Set up environment
cp .env.example .env
# Edit .env — add your API keys (OpenRouter, Groq, Gemini, Serper)

# 3. Start all services
docker compose up --build

# 4. Open dashboard
open http://localhost:3000

# 5. Send your first goal
curl -X POST http://localhost:8000/goal \
  -H "Content-Type: application/json" \
  -d '{"goal": "Build a REST API for user authentication with JWT tokens"}'
```

### Free API Keys

| Provider   | URL                        | Free Tier                    |
|------------|----------------------------|------------------------------|
| OpenRouter | openrouter.ai              | $1 free credit, free models  |
| Groq       | console.groq.com           | Free, rate-limited           |
| Gemini     | aistudio.google.com        | Free tier available          |
| Serper     | serper.dev                 | 2,500 free searches/month    |

### Stopping

```bash
docker compose down          # stop containers
docker compose down -v       # stop + remove volumes (wipes memory/tasks)
```

---

## Project Structure

```
ai-agent-microservices/
├── docker-compose.yml          # 9 containers: agents + redis + postgres + dashboard
├── Dockerfile                  # Shared image for all agent services
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
│
├── postgres-init/
│   └── init.sql                # Schema: tasks, ticket_counters, agent_events
│
├── shared/
│   ├── core/
│   │   ├── config.py           # Pydantic settings + per-role LLM routing
│   │   ├── llm.py              # Unified LLM client (OpenRouter/Groq/Gemini/HF)
│   │   ├── agent_base.py       # BaseAgent with ReAct loop + memory + stream listener
│   │   ├── messaging.py        # Redis Streams MessageBus
│   │   ├── memory.py           # Two-tier AgentMemory + ROLE_SKILLS
│   │   ├── database.py         # Postgres task store (psycopg2)
│   │   └── tool_registry.py    # ToolRegistry singleton
│   └── tools/
│       ├── web_search.py       # Serper.dev search
│       ├── web_browse.py       # HTTP + HTML parsing
│       ├── file_ops.py         # Sandboxed file I/O
│       ├── code_sandbox.py     # Subprocess code execution
│       ├── email_send.py       # SMTP notifications
│       └── task_tools.py       # create/get/update task wrappers
│
├── services/
│   ├── gateway/main.py         # API Gateway (port 8000)
│   ├── manager/
│   │   ├── agent.py            # ManagerAgent + APScheduler
│   │   └── main.py             # FastAPI app (port 8001)
│   ├── developer/
│   │   ├── agent.py            # DeveloperAgent
│   │   └── main.py             # FastAPI app (port 8002)
│   ├── devops/  qa/  support/  docs/
│   │   ├── agent.py
│   │   └── main.py
│
└── dashboard/
    └── index.html              # Live dark-theme dashboard (port 3000)
```

---

## Extending the Team

### Add a new agent role

1. Create `services/newrole/agent.py` — subclass `BaseAgent`, set `role`, `provider`, `required_tools`, `system_prompt`
2. Create `services/newrole/main.py` — FastAPI app (copy from an existing service, change role/port)
3. Add service to `docker-compose.yml` with a new port
4. Add role to `SERVICES` dict in `services/gateway/main.py`
5. Add role skills to `ROLE_SKILLS` in `shared/core/memory.py`
6. Add ticket prefix to `ticket_counters` in `postgres-init/init.sql`

### Add a new tool

1. Create `shared/tools/mytool.py` with `def mytool_executor(params) -> str`
2. Add registration in `shared/tools/__init__.py` via `registry.register("tool_name", schema, executor)`
3. Add `"tool_name"` to `required_tools` in any agent that should use it

### Swap LLM provider

Change `MANAGER_PROVIDER=groq` (or any role) in `.env` and restart that service container.
