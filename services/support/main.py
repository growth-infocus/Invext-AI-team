"""services/support/main.py — SupportAgent: independent container on port 8005"""
import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from shared.core.database import (
    init_schema, get_tasks, create_task, get_task, update_task,
    add_comment, get_comments, get_logs,
)
from shared.tools import register_all
from services.support.agent import SupportAgent
from shared.core.config import settings

log = logging.getLogger("support-service")
agent = SupportAgent()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema(); register_all(); await agent.startup()
    agent.spawn_workers()
    log.info("SupportAgent ready :8005")
    yield

app = FastAPI(title="SupportAgent :8005", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str

class IssueRequest(BaseModel):
    issue: str
    user_email: str = ""
    source: str = "manual"        # "product" triggers P1 auto-escalation
    priority: Optional[str] = None
    ticket_type: str = "Support"

class TicketCreate(BaseModel):
    title: str
    description: str
    source: str = "product"
    priority: str = "P1"
    reporter_email: str = ""
    ticket_type: str = "Support"


@app.get("/health")
async def health():
    return {"service": "support", "status": "ok", "memory": await agent.memory.summary()}


@app.post("/ask")
async def ask(req: AskRequest):
    return {"role": "support", "answer": await agent.ask(req.question)}


@app.post("/issue")
async def handle_issue(req: IssueRequest):
    """
    Accept a support issue from any channel.
    If source='product', the agent will auto-escalate to P1 and notify Teams.
    """
    source_tag = f"\n[SOURCE: {req.source}]" if req.source != "manual" else ""
    priority_tag = f"\n[REQUESTED PRIORITY: {req.priority}]" if req.priority else ""
    escalate_note = (
        "\n\nIMPORTANT: This ticket came from the PRODUCT (source=product). "
        "You MUST create it as ticket_type=Support, source=product (auto-P1) "
        "and immediately call escalate_ticket."
        if req.source == "product" else ""
    )
    prompt = (
        f"User issue: {req.issue}\n"
        f"User: {req.user_email or 'unknown'}"
        f"{source_tag}{priority_tag}{escalate_note}"
    )
    answer = await agent.ask(prompt)
    return {"role": "support", "resolution": answer, "source": req.source}


@app.post("/ticket/product", summary="Raise a P1 support ticket directly from the product")
async def product_ticket(req: TicketCreate):
    """
    Direct endpoint for product-integrated ticket raising.
    Always creates a P1 Support ticket and notifies Teams immediately.
    """
    # Create ticket directly in DB (bypassing agent for speed)
    ticket = create_task(
        title=req.title,
        description=req.description,
        assigned_to="support",
        priority="P1",
        ticket_type=req.ticket_type,
        source="product",
        reporter_email=req.reporter_email,
        created_by="product",
    )

    # Post immediate Teams notification
    try:
        import httpx
        webhook = settings.teams_webhook_url
        if webhook:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(webhook, json={
                    "@type": "MessageCard",
                    "themeColor": "FF0000",
                    "title": f"🚨 P1 PRODUCT TICKET: {ticket['ticket_id']}",
                    "text": (
                        f"**{req.title}**\n\n"
                        f"Reporter: {req.reporter_email or 'product'}\n\n"
                        f"{req.description[:300]}\n\n"
                        f"SLA: {ticket['sla_due_at']}"
                    ),
                    "potentialAction": [{
                        "@type": "OpenUri",
                        "name": "View Ticket",
                        "targets": [{"os": "default", "uri": f"http://localhost:8000/ticket/{ticket['ticket_id']}"}],
                    }],
                })
    except Exception as e:
        log.warning(f"Teams notification failed: {e}")

    # Also ask the agent to investigate asynchronously
    asyncio.create_task(
        agent.ask(
            f"URGENT P1 PRODUCT TICKET CREATED: {ticket['ticket_id']}\n"
            f"Title: {req.title}\n"
            f"Description: {req.description}\n"
            f"Reporter: {req.reporter_email}\n\n"
            f"Immediately: 1) Add a comment acknowledging the issue, "
            f"2) Create a developer task to investigate root cause, "
            f"3) Send email acknowledgement to reporter if email provided."
        )
    )

    return {
        "ticket_id": ticket["ticket_id"],
        "priority": ticket["priority"],
        "status": ticket["status"],
        "sla_due_at": ticket["sla_due_at"],
        "message": f"P1 ticket {ticket['ticket_id']} created — Teams alerted — investigating now",
    }


@app.get("/tickets", summary="List support tickets")
async def list_tickets(status: str = None, priority: str = None, limit: int = 20):
    return {"tickets": get_tasks(assigned_to="support", status=status, priority=priority, limit=limit)}


@app.get("/ticket/{ticket_id}", summary="Get a ticket with comments and activity")
async def get_ticket_detail(ticket_id: str):
    t = get_task(ticket_id)
    if not t:
        raise HTTPException(404, f"Ticket {ticket_id} not found")
    return {
        "ticket":   t,
        "comments": get_comments(t["id"]),
        "activity": get_logs(t["id"]),
    }


@app.post("/ticket/{ticket_id}/comment")
async def comment(ticket_id: str, body: str, author: str = "support"):
    c = add_comment(ticket_id, author=author, body=body)
    if not c:
        raise HTTPException(404, "Ticket not found")
    return {"status": "commented", "comment": c}


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
    return {"tasks": get_tasks(assigned_to="support", status=status, limit=limit)}
