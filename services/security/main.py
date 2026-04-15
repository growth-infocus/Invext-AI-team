"""services/security/main.py — SecurityAgent: independent container on port 8012"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from shared.core.database import init_schema, get_tasks
from shared.tools import register_all
from services.security.agent import SecurityAgent

log = logging.getLogger("security-service")
agent = SecurityAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    agent.spawn_workers()
    log.info("SecurityAgent ready :8012"); yield

app = FastAPI(title="SecurityAgent :8012", lifespan=lifespan)

class AskRequest(BaseModel):
    question: str

class AuditRequest(BaseModel):
    folder_path: str
    depth: str = "full"

@app.get("/health")
async def health():
    return {"service": "security", "status": "ok", "memory": await agent.memory.summary()}

@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "security", "answer": await agent.ask(req.question)}

@app.post("/audit")
async def audit(req: AuditRequest):
    depth_descriptions = {
        "full": "comprehensive security audit scanning all OWASP Top 10 vulnerabilities, code, infrastructure, dependencies, and configurations",
        "quick": "fast security scan focusing on Critical/High severity issues only",
        "dependencies-only": "scan requirements.txt and package.json for vulnerable dependencies and CVEs",
        "owasp": "OWASP Top 10 vulnerability scanning with proof-of-concept for each finding"
    }
    description = depth_descriptions.get(req.depth, depth_descriptions["full"])
    prompt = f"Perform a {description} for the project at {req.folder_path}. Produce security-audit.md with findings by severity. Create P1 tasks for Critical/High issues. Send alert email for any Critical findings."
    return {"role": "security", "audit": await agent.ask(prompt)}

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
    return {"tasks": get_tasks(assigned_to="security", status=status, limit=limit)}
