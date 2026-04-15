"""services/ui_test/main.py — UITestAgent: independent container on port 8009"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.ui_test.agent import UITestAgent

log = logging.getLogger("ui-test-service")
agent = UITestAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    asyncio.create_task(agent.listen())
    log.info("UITestAgent ready :8009"); yield

app = FastAPI(title="UITestAgent :8009", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class RunTestsRequest(BaseModel):
    folder_path: str
    test_type: str = "full"

@app.get("/health")
async def health():
    return {"service": "ui_test", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "ui_test", "answer": await agent.ask(req.question)}

@app.post("/run-tests")
async def run_tests(req: RunTestsRequest):
    prompt = f"Write and run UI tests for the project at {req.folder_path}. Test type: {req.test_type} (full|smoke|regression|accessibility)"
    return {"role": "ui_test", "results": await agent.ask(prompt)}

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
    return {"tasks": get_tasks(assigned_to="ui_test", status=status, limit=limit)}
