"""services/docs/main.py — DocsAgent: independent container on port 8006"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.docs.agent import DocsAgent

log = logging.getLogger("docs-service")
agent = DocsAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("DocsAgent ready :8006"); yield

app = FastAPI(title="DocsAgent :8006", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

@app.get("/health")
async def health():
    return {"service": "docs", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "docs", "answer": await agent.ask(req.question)}

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
    return {"tasks": get_tasks(assigned_to="docs", status=status, limit=limit)}
