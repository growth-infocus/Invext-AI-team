"""services/developer/main.py — DeveloperAgent: independent container on port 8002"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.developer.agent import DeveloperAgent

log = logging.getLogger("developer-service")
agent = DeveloperAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    agent.spawn_workers()
    log.info("DeveloperAgent ready :8002"); yield

app = FastAPI(title="DeveloperAgent :8002", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

@app.get("/health")
async def health():
    return {"service": "developer", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "developer", "answer": await agent.ask(req.question)}

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
    return {"tasks": get_tasks(assigned_to="developer", status=status, limit=limit)}
