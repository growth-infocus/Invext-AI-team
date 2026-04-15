"""services/qa/main.py — QAAgent: independent container on port 8004"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.qa.agent import QAAgent

log = logging.getLogger("qa-service")
agent = QAAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("QAAgent ready :8004"); yield

app = FastAPI(title="QAAgent :8004", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

@app.get("/health")
async def health():
    return {"service": "qa", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "qa", "answer": await agent.ask(req.question)}

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
    return {"tasks": get_tasks(assigned_to="qa", status=status, limit=limit)}
