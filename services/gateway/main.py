"""
services/gateway/main.py — API Gateway (port 8000)
Single entry point for humans, the product, and external apps.
"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from pydantic import BaseModel
from typing import Literal, Optional, List

from shared.obsidian_export.exporter import export_from_redis_memory
from shared.core.database import (
    init_schema,
    get_tasks, get_task, create_task, update_task, add_comment, get_comments, get_logs,
    get_deployments, get_environments, get_pipeline_runs,
)

log = logging.getLogger("gateway")

SERVICES = {
    "manager":   "http://manager:8001",
    "developer": "http://developer:8002",
    "devops":    "http://devops:8003",
    "qa":        "http://qa:8004",
    "support":   "http://support:8005",
    "docs":      "http://docs:8006",
    "design":    "http://design:8007",
    "ux":        "http://ux:8008",
    "ui_test":   "http://ui_test:8009",
    "api_test":  "http://api_test:8010",
    "qa_auto":   "http://qa_auto:8011",
    "security":  "http://security:8012",
}

ALL_ROLES = Literal[
    "manager","developer","devops","qa","support","docs",
    "design","ux","ui_test","api_test","qa_auto","security"
]

import httpx

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    log.info("Gateway ready :8000")
    yield

app = FastAPI(title="AI Agent Team — Gateway :8000", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Proxy helper ──────────────────────────────────────────────────────────────

async def _proxy(service: str, path: str, method: str = "GET", json: dict = None):
    url = SERVICES[service] + path
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(url, json=json) if method == "POST" else await c.get(url)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()

async def _proxy_patch(service: str, path: str, json: dict = None):
    url = SERVICES[service] + path
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.patch(url, json=json)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)
    return r.json()


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    p = Path("/app/dashboard/index.html")
    return HTMLResponse(p.read_text() if p.exists() else "<h1>AI Agent Team</h1>")


# ── Core agent endpoints ──────────────────────────────────────────────────────

class GoalRequest(BaseModel):
    goal: str

class AskRequest(BaseModel):
    question: str

class IssueRequest(BaseModel):
    issue: str
    user_email: str = ""
    source: str = "manual"
    priority: Optional[str] = None
    ticket_type: str = "Support"


@app.post("/goal", summary="Give the team a goal to plan and execute")
async def goal(req: GoalRequest):
    return await _proxy("manager", "/goal", "POST", {"goal": req.goal})

@app.post("/ask/{role}", summary="Ask any agent a direct question")
async def ask(role: ALL_ROLES, req: AskRequest):
    if role not in SERVICES:
        raise HTTPException(404, f"Unknown role: {role}")
    return await _proxy(role, "/ask", "POST", {"question": req.question})

@app.post("/issue", summary="Submit a support issue")
async def issue(req: IssueRequest):
    return await _proxy("support", "/issue", "POST", req.dict())

@app.get("/status", summary="Team status overview")
async def status():
    return await _proxy("manager", "/status")

@app.get("/memory/{role}", summary="View an agent's memory")
async def memory(role: ALL_ROLES):
    return await _proxy(role, "/memory")

@app.get("/health", summary="All services health check")
async def health():
    results = {}
    async with httpx.AsyncClient(timeout=5) as c:
        for role, url in SERVICES.items():
            try:
                r = await c.get(url + "/health")
                results[role] = "ok" if r.status_code == 200 else f"error:{r.status_code}"
            except Exception as e:
                results[role] = f"down:{e}"
    overall = "ok" if all(v == "ok" for v in results.values()) else "degraded"
    return {"overall": overall, "services": results}


# ── Tickets ───────────────────────────────────────────────────────────────────

class TicketCreateRequest(BaseModel):
    title: str
    description: str
    assigned_to: str = "support"
    ticket_type: str = "Task"
    priority: str = "P3"
    source: str = "manual"
    reporter_email: str = ""
    labels: str = ""
    acceptance: str = ""
    sprint_id: Optional[str] = None
    parent_id: Optional[str] = None

class TicketUpdateRequest(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    result: Optional[str] = None
    assigned_to: Optional[str] = None

class CommentRequest(BaseModel):
    body: str
    author: str = "user"

class ProductTicketRequest(BaseModel):
    title: str
    description: str
    reporter_email: str = ""
    ticket_type: str = "Support"


@app.post("/tickets", summary="Create a ticket (any type)")
async def create_ticket(req: TicketCreateRequest):
    ticket = create_task(
        title=req.title,
        description=req.description,
        assigned_to=req.assigned_to,
        priority=req.priority,
        ticket_type=req.ticket_type,
        source=req.source,
        reporter_email=req.reporter_email,
        acceptance_criteria=req.acceptance,
        labels=req.labels,
        sprint_id=req.sprint_id,
        parent_task_id=req.parent_id,
        created_by="gateway",
    )
    return ticket


@app.post("/tickets/product", summary="Raise a P1 product ticket — auto-escalated immediately")
async def product_ticket(req: ProductTicketRequest):
    """
    Call this from your product when users hit critical issues.
    Always creates P1 Support ticket and fires Teams alert within seconds.
    """
    return await _proxy("support", "/ticket/product", "POST", req.dict())


@app.get("/tickets", summary="List / search tickets")
async def list_tickets(
    assigned_to: str = None,
    status: str = None,
    priority: str = None,
    ticket_type: str = None,
    source: str = None,
    limit: int = 50,
):
    tickets = get_tasks(
        assigned_to=assigned_to,
        status=status,
        priority=priority,
        ticket_type=ticket_type,
        source=source,
        limit=limit,
    )
    return {"tickets": tickets, "total": len(tickets)}


@app.get("/tickets/{ticket_id}", summary="Get ticket details with comments and activity")
async def get_ticket(ticket_id: str):
    t = get_task(ticket_id)
    if not t:
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    return {
        "ticket":   t,
        "comments": get_comments(t["id"]),
        "activity": get_logs(t["id"]),
    }


@app.patch("/tickets/{ticket_id}", summary="Update a ticket")
async def update_ticket_endpoint(ticket_id: str, req: TicketUpdateRequest):
    update_task(
        task_id=ticket_id,
        status=req.status,
        result=req.result,
        assigned_to=req.assigned_to,
        priority=req.priority,
    )
    return {"status": "updated", "ticket_id": ticket_id}


@app.post("/tickets/{ticket_id}/comments", summary="Add a comment to a ticket")
async def add_ticket_comment(ticket_id: str, req: CommentRequest):
    c = add_comment(ticket_id, author=req.author, body=req.body)
    if not c:
        raise HTTPException(404, "Ticket not found")
    return {"status": "commented", "comment": c}


@app.get("/tasks", summary="All tasks (legacy)")
async def tasks(assigned_to: str = None, status: str = None):
    url = "/tasks"
    params = []
    if assigned_to: params.append(f"assigned_to={assigned_to}")
    if status: params.append(f"status={status}")
    if params: url += "?" + "&".join(params)
    return await _proxy("manager", url)


# ── DevOps management ─────────────────────────────────────────────────────────

class DeployRequest(BaseModel):
    service_name: str
    version: str
    environment: str = "staging"
    branch: str = "main"
    commit_sha: str = ""
    notes: str = ""

class PipelineRequest(BaseModel):
    branch: str = "main"
    triggered_by: str = "user"
    steps: Optional[List[dict]] = None


@app.get("/devops/overview", summary="Full DevOps infrastructure overview")
async def devops_overview():
    return await _proxy("devops", "/infrastructure")

@app.get("/devops/environments", summary="Environment statuses")
async def devops_environments():
    return await _proxy("devops", "/environments")

@app.get("/devops/deployments", summary="Recent deployments")
async def devops_deployments(environment: str = None, status: str = None, limit: int = 20):
    url = f"/deployments?limit={limit}"
    if environment: url += f"&environment={environment}"
    if status:      url += f"&status={status}"
    return await _proxy("devops", url)

@app.post("/devops/deploy", summary="Trigger a deployment")
async def devops_deploy(req: DeployRequest):
    return await _proxy("devops", "/deployments", "POST", req.dict())

@app.get("/devops/pipelines", summary="CI/CD pipeline history")
async def devops_pipelines(pipeline: str = None, limit: int = 10):
    url = f"/pipelines?limit={limit}"
    if pipeline: url += f"&pipeline={pipeline}"
    return await _proxy("devops", url)

@app.post("/devops/pipelines/{pipeline}", summary="Trigger a CI/CD pipeline")
async def trigger_pipeline(pipeline: str, req: PipelineRequest):
    return await _proxy("devops", f"/pipelines/{pipeline}", "POST", req.dict())

@app.post("/devops/incident", summary="Raise a P1 infrastructure incident")
async def devops_incident(title: str, description: str):
    return await _proxy("devops", "/incident", "POST", None)


# ── Export / Obsidian ─────────────────────────────────────────────────────────

@app.get("/export/obsidian/{role}", summary="Export agent memories to Obsidian vault")
async def export_obsidian(role: ALL_ROLES):
    if role not in SERVICES:
        raise HTTPException(404, f"Unknown role: {role}")
    try:
        written = await export_from_redis_memory(role)
        return {"role": role, "written": written, "status": "ok",
                "message": f"Exported {written} memories to obsidian_vault/{role}"}
    except Exception as e:
        log.error(f"Export failed for {role}: {e}")
        raise HTTPException(500, f"Export failed: {e}")


# ── Project analysis ──────────────────────────────────────────────────────────

class ProjectAnalysisRequest(BaseModel):
    folder_path: str
    agents: list = ["design", "ux", "security", "api_test", "ui_test", "qa_auto"]

@app.post("/analyze-project", summary="Trigger all specialist agents to audit a project folder")
async def analyze_project(req: ProjectAnalysisRequest):
    audit_agents = {
        "design":   ("/audit",       {"folder_path": req.folder_path}),
        "ux":       ("/audit",       {"folder_path": req.folder_path}),
        "security": ("/audit",       {"folder_path": req.folder_path, "depth": "full"}),
        "api_test": ("/run-tests",   {"folder_path": req.folder_path}),
        "ui_test":  ("/run-tests",   {"folder_path": req.folder_path}),
        "qa_auto":  ("/build-suite", {"folder_path": req.folder_path}),
    }

    async def trigger(role, path, body):
        try:
            return role, await _proxy(role, path, "POST", body)
        except Exception as e:
            return role, {"error": str(e)}

    tasks = [
        trigger(role, path, body)
        for role, (path, body) in audit_agents.items()
        if role in req.agents and role in SERVICES
    ]
    responses = await asyncio.gather(*tasks)
    results = {role: resp for role, resp in responses}

    try:
        results["manager"] = await _proxy("manager", "/goal", "POST", {
            "goal": (
                f"Project analysis triggered on folder: {req.folder_path}\n"
                f"Agents auditing: {', '.join(req.agents)}\n"
                f"Monitor findings, prioritise bugs, and coordinate fixes."
            )
        })
    except Exception as e:
        results["manager"] = {"error": str(e)}

    return {
        "folder_path": req.folder_path,
        "triggered_agents": list(results.keys()),
        "responses": results,
    }


# ── Standup / Meetings ────────────────────────────────────────────────────────

@app.get("/standup/now", summary="Trigger daily standup immediately")
async def standup_now():
    return await _proxy("manager", "/standup/now")

@app.get("/scheduler/jobs", summary="List all scheduled jobs")
async def scheduler_jobs():
    return await _proxy("manager", "/scheduler/jobs")

@app.post("/meeting/join", summary="Ask agents to join a Teams meeting")
async def meeting_join(url: str = "", topic: str = "Team meeting"):
    return await _proxy("manager", "/ask", "POST", {
        "question": (
            f"The team has been asked to join a Teams meeting.\n"
            f"Meeting URL: {url}\nTopic: {topic}\n"
            f"Post a message to Teams that you are joining the meeting and ready to discuss "
            f"the current team status and any blockers."
        )
    })

@app.post("/meeting/end", summary="End meeting — post summary to Teams")
async def meeting_end():
    return await _proxy("manager", "/ask", "POST", {
        "question": (
            "The team meeting has ended. "
            "Post a meeting summary to Teams covering: decisions made, action items, "
            "next steps, and who owns what. Be concise."
        )
    })
