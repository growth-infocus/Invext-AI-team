"""services/manager/main.py — ManagerAgent: independent container on port 8001"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.manager.agent import ManagerAgent
from services.manager.scheduler import create_scheduler

log = logging.getLogger("manager-service")
agent = ManagerAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    scheduler = create_scheduler()
    scheduler.start()
    log.info("⏰ Scheduler started — 24/7 autonomous operation active")
    log.info("ManagerAgent ready :8001")
    yield
    scheduler.shutdown()

app = FastAPI(title="ManagerAgent :8001", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class GoalRequest(BaseModel):
    goal: str

@app.get("/health")
async def health():
    return {"service": "manager", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "manager", "answer": await agent.ask(req.question)}

@app.post("/goal")
async def submit_goal(req: GoalRequest):
    return {"status": "planned", "plan": await agent.plan_and_delegate(req.goal)}

@app.get("/status")
async def status():
    return {"summary": await agent.get_team_status()}

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
    return {"tasks": get_tasks(assigned_to="manager", status=status, limit=limit)}

@app.get("/standup/now", summary="Trigger standup generation immediately")
async def standup_now():
    """Manually trigger the standup — useful for on-demand team meetings."""
    from services.manager.scheduler import job_daily_standup
    asyncio.create_task(job_daily_standup())
    return {"status": "standup triggered", "message": "Generating standup for all 12 agents and posting to Teams"}

@app.get("/scheduler/jobs", summary="List all scheduled jobs and next run times")
async def scheduler_jobs():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    # Return static schedule since scheduler instance isn't accessible here
    return {
        "jobs": [
            {"id": "daily_standup",   "schedule": "09:00 UTC daily",     "description": "All agents post standup to Teams"},
            {"id": "delegation_loop", "schedule": "Every 30 minutes",    "description": "Assign pending tasks to agents"},
            {"id": "sla_check",       "schedule": "Every hour",          "description": "Alert on overdue P1/P2 tickets"},
            {"id": "daily_report",    "schedule": "17:00 UTC daily",     "description": "Daily summary to Teams + email"},
            {"id": "weekly_report",   "schedule": "Monday 09:00 UTC",    "description": "Weekly recap to Teams + email"},
            {"id": "health_check",    "schedule": "Every 5 minutes",     "description": "Verify all 12 agents reachable"},
        ]
    }
