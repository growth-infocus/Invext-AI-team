"""services/qa_auto/main.py — QAAutoAgent: independent container on port 8011"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.qa_auto.agent import QAAutoAgent

log = logging.getLogger("qa-auto-service")
agent = QAAutoAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    agent.spawn_workers()
    log.info("QAAutoAgent ready :8011"); yield

app = FastAPI(title="QAAutoAgent :8011", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class BuildSuiteRequest(BaseModel):
    folder_path: str
    stack: str = "auto-detect"

@app.get("/health")
async def health():
    return {"service": "qa_auto", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "qa_auto", "answer": await agent.ask(req.question)}

@app.post("/build-suite")
async def build_suite(req: BuildSuiteRequest):
    prompt = f"Build a comprehensive test suite for the project at {req.folder_path}. Stack: {req.stack}. Create unit, integration, E2E, and contract tests. Set up conftest.py, fixtures, and test runners. Measure coverage and produce qa-automation-report.md."
    return {"role": "qa_auto", "result": await agent.ask(prompt)}

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
    return {"tasks": get_tasks(assigned_to="qa_auto", status=status, limit=limit)}
