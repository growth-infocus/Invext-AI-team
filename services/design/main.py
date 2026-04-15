"""services/design/main.py — DesignAgent: independent container on port 8007"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.design.agent import DesignAgent

log = logging.getLogger("design-service")
agent = DesignAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("DesignAgent ready :8007"); yield

app = FastAPI(title="DesignAgent :8007", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class AuditRequest(BaseModel):
    folder_path: str
    focus: str = "full"

@app.get("/health")
async def health():
    return {"service": "design", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "design", "answer": await agent.ask(req.question)}

@app.post("/audit")
async def audit(req: AuditRequest):
    instruction = f"Scan the folder {req.folder_path} and produce a comprehensive design-audit.md. Focus: {req.focus}"
    return {"role": "design", "audit": await agent.ask(instruction)}

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
    return {"tasks": get_tasks(assigned_to="design", status=status, limit=limit)}
