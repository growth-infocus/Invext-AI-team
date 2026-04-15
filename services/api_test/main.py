"""services/api_test/main.py — APITestAgent: independent container on port 8010"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.api_test.agent import APITestAgent

log = logging.getLogger("api-test-service")
agent = APITestAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("APITestAgent ready :8010"); yield

app = FastAPI(title="APITestAgent :8010", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class RunTestsRequest(BaseModel):
    folder_path: str
    base_url: str = "http://localhost:8000"

@app.get("/health")
async def health():
    return {"service": "api_test", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "api_test", "answer": await agent.ask(req.question)}

@app.post("/run-tests")
async def run_tests(req: RunTestsRequest):
    prompt = f"Discover and test all APIs in the project at {req.folder_path} with base_url {req.base_url}"
    return {"role": "api_test", "results": await agent.ask(prompt)}

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
    return {"tasks": get_tasks(assigned_to="api_test", status=status, limit=limit)}
