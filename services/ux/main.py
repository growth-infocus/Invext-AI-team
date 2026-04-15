"""services/ux/main.py — UXAgent: independent container on port 8008"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.ux.agent import UXAgent

log = logging.getLogger("ux-service")
agent = UXAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    agent.spawn_workers()
    log.info("UXAgent ready :8008"); yield

app = FastAPI(title="UXAgent :8008", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class AuditRequest(BaseModel):
    folder_path: str

@app.get("/health")
async def health():
    return {"service": "ux", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "ux", "answer": await agent.ask(req.question)}

@app.post("/audit")
async def audit(req: AuditRequest):
    instruction = f"Scan the folder {req.folder_path} and produce ux-audit.md and user-flows.md with comprehensive flow mapping and UX issues"
    return {"role": "ux", "audit": await agent.ask(instruction)}

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
    return {"tasks": get_tasks(assigned_to="ux", status=status, limit=limit)}
