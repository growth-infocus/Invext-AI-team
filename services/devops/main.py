"""services/devops/main.py — DevOpsAgent: independent container on port 8003"""
import asyncio, logging, uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from shared.core.database import (
    init_schema, get_tasks,
    create_deployment, get_deployments, update_deployment,
    get_environments, update_environment,
    create_pipeline_run, get_pipeline_runs,
    get_sla_breached,
)
from shared.tools import register_all
from services.devops.agent import DevOpsAgent

log = logging.getLogger("devops-service")
agent = DevOpsAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("DevOpsAgent ready :8003")
    yield

app = FastAPI(title="DevOpsAgent :8003", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str

class DeployRequest(BaseModel):
    service_name: str
    version: str
    environment: str = "staging"
    branch: str = "main"
    commit_sha: str = ""
    notes: str = ""

class UpdateDeployRequest(BaseModel):
    status: str                          # running | success | failed | rolled_back
    notes: str = ""

class EnvUpdateRequest(BaseModel):
    status: str                          # healthy | degraded | down | deploying | unknown
    url: Optional[str] = None
    last_deploy: Optional[str] = None

class PipelineRequest(BaseModel):
    branch: str = "main"
    triggered_by: str = "devops"
    steps: Optional[List[dict]] = None


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    envs = get_environments()
    env_status = {e["name"]: e["status"] for e in envs}
    return {
        "service": "devops",
        "status": "ok",
        "environments": env_status,
        "memory": await agent.memory.summary(),
    }


@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "devops", "answer": await agent.ask(req.question)}


# ── Deployments ───────────────────────────────────────────────────────────────

@app.post("/deployments", summary="Trigger a new deployment")
async def new_deployment(req: DeployRequest):
    d = create_deployment(
        service_name=req.service_name,
        version=req.version,
        environment=req.environment,
        branch=req.branch,
        commit_sha=req.commit_sha,
        notes=req.notes,
        triggered_by="devops-api",
    )
    # Ask the agent to write a runbook asynchronously
    asyncio.create_task(
        agent.ask(
            f"Deployment started: {req.service_name} v{req.version} → {req.environment}\n"
            f"Deploy ID: {d['id']}\nBranch: {req.branch}\nCommit: {req.commit_sha}\n"
            f"Write a runbook for this deployment including rollback procedure. "
            f"Update the deployment status once runbook is ready."
        )
    )
    return d


@app.get("/deployments", summary="List deployments")
async def list_deployments(environment: str = None, status: str = None, limit: int = 20):
    return {"deployments": get_deployments(environment=environment, status=status, limit=limit)}


@app.patch("/deployments/{deploy_id}", summary="Update deployment status")
async def patch_deployment(deploy_id: str, req: UpdateDeployRequest):
    update_deployment(deploy_id, req.status, req.notes)
    # Update environment health automatically
    recent = get_deployments(limit=1)
    if recent and recent[0]["id"] == deploy_id:
        env_status = "healthy" if req.status == "success" else "degraded" if req.status == "failed" else "deploying"
        update_environment(
            name=recent[0]["environment"],
            status=env_status,
            last_deploy=recent[0]["version"] if req.status == "success" else None,
        )
    return {"status": "updated", "deploy_id": deploy_id, "new_status": req.status}


# ── Environments ──────────────────────────────────────────────────────────────

@app.get("/environments", summary="Get all environment statuses")
async def list_environments():
    return {"environments": get_environments()}


@app.patch("/environments/{name}", summary="Update environment status")
async def update_env(name: str, req: EnvUpdateRequest):
    if name not in ("development", "staging", "production"):
        raise HTTPException(400, f"Unknown environment: {name}")
    update_environment(name=name, status=req.status, url=req.url, last_deploy=req.last_deploy)
    return {"status": "updated", "environment": name, "new_status": req.status}


# ── Pipelines ─────────────────────────────────────────────────────────────────

@app.post("/pipelines/{pipeline}", summary="Trigger a CI/CD pipeline")
async def trigger_pipeline(pipeline: str, req: PipelineRequest):
    run = create_pipeline_run(
        pipeline=pipeline,
        branch=req.branch,
        triggered_by=req.triggered_by,
        steps=req.steps or [
            {"name": "lint",   "status": "pending"},
            {"name": "test",   "status": "pending"},
            {"name": "build",  "status": "pending"},
            {"name": "deploy", "status": "pending"},
        ],
    )
    asyncio.create_task(
        agent.ask(
            f"Pipeline '{pipeline}' triggered on branch '{req.branch}'. "
            f"Run ID: {run['id']}\n"
            f"Monitor the steps and update the pipeline run status when complete. "
            f"Create a deployment record if the deploy step succeeds."
        )
    )
    return run


@app.get("/pipelines", summary="List pipeline run history")
async def list_pipelines(pipeline: str = None, limit: int = 10):
    return {"runs": get_pipeline_runs(pipeline=pipeline, limit=limit)}


# ── Infrastructure overview ───────────────────────────────────────────────────

@app.get("/infrastructure", summary="Full infrastructure overview")
async def infrastructure_overview():
    envs = get_environments()
    recent_deploys = get_deployments(limit=5)
    recent_pipelines = get_pipeline_runs(limit=5)
    sla_breached = get_sla_breached()
    devops_tasks = get_tasks(assigned_to="devops", status="in_progress", limit=10)

    return {
        "environments":       envs,
        "recent_deployments": recent_deploys,
        "recent_pipelines":   recent_pipelines,
        "sla_breached":       len(sla_breached),
        "active_tasks":       devops_tasks,
    }


@app.post("/incident", summary="Raise a P1 infrastructure incident")
async def raise_incident(title: str, description: str):
    answer = await agent.ask(
        f"PRODUCTION INCIDENT: {title}\n{description}\n\n"
        f"1. Create a P1 Incident ticket immediately\n"
        f"2. Investigate the cause\n"
        f"3. Post a Teams notification\n"
        f"4. Suggest immediate mitigation steps"
    )
    return {"role": "devops", "incident_response": answer}


# ── Standard endpoints ────────────────────────────────────────────────────────

@app.get("/memory")
async def get_memory():
    return await agent.memory.summary()

@app.post("/memory/remember")
async def remember(fact: str):
    await agent.memory.remember(fact); return {"status": "remembered"}

@app.post("/memory/learn-skill")
async def learn_skill(skill: str):
    await agent.memory.add_skill(skill); return {"status": "skill learned", "skill": skill}

@app.get("/tasks")
async def my_tasks(status: str = None, limit: int = 20):
    return {"tasks": get_tasks(assigned_to="devops", status=status, limit=limit)}
