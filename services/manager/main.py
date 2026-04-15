"""services/manager/main.py — ManagerAgent: independent container on port 8001"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.core.database import init_schema, get_tasks
from shared.core.plan_store import (
    load_plan, update_plan_status, list_plans, plan_summary, delete_plan
)
from shared.core.agent_base import (
    list_pending_workplans, list_all_workplans,
    get_workplan, set_workplan_status,
)
from shared.tools import register_all
from services.manager.agent import ManagerAgent
from services.manager.scheduler import create_scheduler

log = logging.getLogger("manager-service")
agent = ManagerAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    register_all()
    await agent.startup()
    asyncio.create_task(agent.listen())
    scheduler = create_scheduler()
    scheduler.start()
    log.info("ManagerAgent ready :8001 — planning system active")
    yield
    scheduler.shutdown()


app = FastAPI(title="ManagerAgent :8001", lifespan=lifespan)


# ── Request models ────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str

class GoalRequest(BaseModel):
    goal: str

class PlanCreateRequest(BaseModel):
    goal: str
    project_source: Optional[str] = None  # local path, GitHub URL, or production URL

class PlanApproveRequest(BaseModel):
    action: str              # "approve" | "reject" | "revise"
    feedback: Optional[str] = None   # revision instructions or rejection reason


# ── Health / misc ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"service": "manager", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "manager", "answer": await agent.ask(req.question)}

@app.get("/status")
async def status():
    return {"summary": await agent.get_team_status()}

@app.get("/memory")
async def get_memory():
    return await agent.memory.summary()

@app.post("/memory/remember")
async def remember(fact: str):
    await agent.memory.remember(fact)
    return {"status": "remembered"}

@app.post("/memory/learn-skill")
async def learn_skill(skill: str):
    await agent.memory.add_skill(skill)
    return {"status": "skill learned", "skill": skill}

@app.get("/tasks")
async def my_tasks(status: str = None, limit: int = 20):
    return {"tasks": get_tasks(assigned_to="manager", status=status, limit=limit)}


# ── Rapid goal → delegate (no approval step) ─────────────────────────────────

@app.post("/goal")
async def submit_goal(req: GoalRequest):
    return {"status": "planned", "plan": await agent.plan_and_delegate(req.goal)}


# ── Expert planning workflow ──────────────────────────────────────────────────

@app.post("/plan/create", summary="Phase 1: Generate a project plan for review")
async def plan_create(req: PlanCreateRequest):
    """
    Generate a full project plan from a goal + optional project source.
    Returns the plan for human review. No tickets are created yet.

    project_source can be:
      - A local folder path: /app/sandbox/myproject
      - A GitHub URL:        https://github.com/owner/repo
      - A production URL:    https://myapp.com
    """
    result = await agent.create_project_plan(
        goal=req.goal,
        project_source=req.project_source,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.get("/plan/{plan_id}", summary="Get a plan by ID")
async def plan_get(plan_id: str):
    plan = load_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return plan


@app.get("/plans", summary="List all plans")
async def plans_list(status: Optional[str] = None, limit: int = 20):
    plans = list_plans(limit=limit, status_filter=status)
    return {"plans": [plan_summary(p) for p in plans], "total": len(plans)}


@app.post("/plan/{plan_id}/approve", summary="Phase 2: Approve, reject, or request revisions")
async def plan_approve(plan_id: str, req: PlanApproveRequest):
    """
    action='approve'  → creates all tickets and dispatches to agents
    action='reject'   → marks plan as rejected (no tickets created)
    action='revise'   → re-runs planning with original goal + feedback appended
    """
    plan = load_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    if req.action == "approve":
        update_plan_status(plan_id, "approved")
        result = await agent.execute_plan(plan_id)
        return {"status": "executing", **result}

    elif req.action == "reject":
        update_plan_status(plan_id, "rejected")
        return {"status": "rejected", "plan_id": plan_id, "feedback": req.feedback}

    elif req.action == "revise":
        if not req.feedback:
            raise HTTPException(status_code=400, detail="feedback is required for revisions")
        # Re-run planning with revision feedback appended to the original goal
        original_goal    = plan.get("goal", "")
        revised_goal     = f"{original_goal}\n\n[REVISION REQUEST]: {req.feedback}"
        project_source   = plan.get("source_url") or None
        update_plan_status(plan_id, "revision_requested")
        new_plan = await agent.create_project_plan(
            goal=revised_goal,
            project_source=project_source,
        )
        if "error" in new_plan:
            raise HTTPException(status_code=500, detail=new_plan["error"])
        return {"status": "revised", "original_plan_id": plan_id, "new_plan": new_plan}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action '{req.action}'. Use: approve | reject | revise")


@app.delete("/plan/{plan_id}", summary="Delete a plan")
async def plan_delete(plan_id: str):
    if not delete_plan(plan_id):
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return {"status": "deleted", "plan_id": plan_id}


# ── Employee work-plan review (manager approves/rejects agent plans) ──────────

class WorkplanReviewRequest(BaseModel):
    action:   str             # approve | reject
    feedback: Optional[str] = ""


@app.get("/workplans", summary="List all pending employee work plans awaiting manager review")
async def list_workplans_pending():
    """Returns all agent work plans with status=pending_manager_review."""
    return {"workplans": list_pending_workplans()}


@app.get("/workplans/all", summary="List all employee work plans (any status)")
async def list_workplans_all(status: Optional[str] = None):
    return {"workplans": list_all_workplans(status_filter=status)}


@app.get("/workplans/{role}/{task_id}", summary="Get a specific agent work plan")
async def get_agent_workplan(role: str, task_id: str):
    plan = get_workplan(role, task_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Work plan not found")
    return plan


@app.post("/workplans/{role}/{task_id}/review",
          summary="Approve or reject an agent's work plan")
async def review_workplan(role: str, task_id: str, req: WorkplanReviewRequest):
    """
    action='approve' — agent will proceed with execution
    action='reject'  — agent will be blocked; provide feedback with revision guidance
    """
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")
    ok = set_workplan_status(role, task_id, req.action + "d", feedback=req.feedback or "")
    if not ok:
        raise HTTPException(status_code=404, detail="Work plan not found")
    return {
        "status":   req.action + "d",
        "role":     role,
        "task_id":  task_id,
        "feedback": req.feedback,
    }


# ── Scheduler / standup ───────────────────────────────────────────────────────

@app.get("/standup/now")
async def standup_now():
    from services.manager.scheduler import job_daily_standup
    asyncio.create_task(job_daily_standup())
    return {"status": "standup triggered"}

@app.get("/scheduler/jobs")
async def scheduler_jobs():
    return {
        "jobs": [
            {"id": "daily_standup",   "schedule": "09:00 UTC daily",  "description": "All agents post standup to Teams"},
            {"id": "delegation_loop", "schedule": "Every 30 minutes", "description": "Assign pending tasks to agents"},
            {"id": "sla_check",       "schedule": "Every hour",       "description": "Alert on overdue P1/P2 tickets"},
            {"id": "daily_report",    "schedule": "17:00 UTC daily",  "description": "Daily summary to Teams + email"},
            {"id": "weekly_report",   "schedule": "Monday 09:00 UTC", "description": "Weekly recap to Teams + email"},
            {"id": "health_check",    "schedule": "Every 5 minutes",  "description": "Verify all 12 agents reachable"},
            {"id": "idle_work",       "schedule": "Every 20 minutes", "description": "Give learning/maintenance tasks to idle agents"},
        ]
    }
